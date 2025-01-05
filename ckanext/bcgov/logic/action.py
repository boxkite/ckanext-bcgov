# Copyright  2015, Province of British Columbia
# License: https://github.com/bcgov/ckanext-bcgov/blob/master/license

import ckan.plugins.toolkit as toolkit

import logging
import datetime
import sqlalchemy
import json

import ckan.logic as logic
import ckan.plugins as plugins

import smtplib
from time import time
import ckan.lib as lib
from email import utils
from email.mime.text import MIMEText
from email.header import Header
from ckan.plugins.toolkit import config
from ckan.common import _, c, g
import ckan.lib.plugins as lib_plugins
import ckan.lib.dictization.model_save as model_save
import ckan.lib.dictization.model_dictize as model_dictize

import ckan.lib.uploader as uploader
import ckan.lib.helpers as h
import ckan.lib.munge as munge
import paste.deploy.converters

from ckan.lib.mailer import MailerException
import ckan.model as model

from ckanext.bcgov.util.util import (get_organization_branches, get_parent_orgs)

import pprint

NotFound = logic.NotFound
NotAuthorized = logic.NotAuthorized
ValidationError = logic.ValidationError
_get_or_bust = logic.get_or_bust


# shortcuts
get_action = logic.get_action
_check_access = logic.check_access
NotFound = logic.NotFound
_validate = lib.navl.dictization_functions.validate
ValidationError = logic.ValidationError
_get_action = logic.get_action

log = logging.getLogger('ckanext.edc_schema')

_or_ = sqlalchemy.or_

@toolkit.side_effect_free
def whoami(context, data_dict):
    '''Get the user id for the currently logged in user

    :rtype string'''


    return toolkit.c.user


'''
Checking package status and sending a notification if the state is changed.
'''

def get_msg_content(msg_dict):

    from string import Template

    msg = ('<p>As a BC Data Catalogue $user_role for $org, '
           'please be advised that the publication state of this record is now "$dataset_state" (previously $prev_state):<br /><br />'
           '<b>$name</b> (<a href="$dataset_url">$dataset_url</a>)<br />'
           'If you are no longer an $user_role for $org or if you have a question or concern regarding this message '
           'please open a ticket with <a href="&quot;https://dpdd.atlassian.net/servicedesk/customer/portal/1">Data Systems and Services request system</a> .<br><br>Thanks.</p>')

    msg_template = Template(msg)

    return msg_template.substitute(msg_dict)


def add_msg_niceties(recipient_name, body, sender_name, sender_url):
    return "Dear %s,<br><br>" % recipient_name \
           + "\r\n\r\n%s\r\n\r\n" % body \
           + "<br><br>--<br>\r\n%s (<a href=\"%s\">%s</a>)" % (
               sender_name, sender_url, sender_url)


def send_state_change_notifications(members, email_dict, sender_name, sender_url):
    '''
    Sends state change notifications to sub-org members.
    Updated by Khalegh Mamakani on March 5 2015.
    List of changes :
        - Creating the smtp connection once for all notifications instead of connecting and disconnecting for
          every single recipient.
        - Using a thread to send the notifications in the background.
    '''

    # Email common fields
    subject = email_dict['subject']

    mail_from = config.get('smtp.mail_from')
    subject = Header(subject.encode('utf-8'), 'utf-8')

    # Connecting to smtp server.
    smtp_connection = smtplib.SMTP()
    if 'smtp.test_server' in config:
        # If 'smtp.test_server' is configured we assume we're running tests,
        # and don't use the smtp.server, starttls, user, password etc. options.
        smtp_server = config['smtp.test_server']
        smtp_starttls = False
        smtp_user = None
        smtp_password = None
    else:
        smtp_server = config.get('smtp.server', 'localhost')
        smtp_starttls = paste.deploy.converters.asbool(
            config.get('smtp.starttls'))
        smtp_user = config.get('smtp.user')
        smtp_password = config.get('smtp.password')
    smtp_connection.connect(smtp_server)
    try:

        # Identify ourselves and prompt the server for supported features.
        smtp_connection.ehlo()

        # If 'smtp.starttls' is on in CKAN config, try to put the SMTP
        # connection into TLS mode.
        if smtp_starttls:
            if smtp_connection.has_extn('STARTTLS'):
                smtp_connection.starttls()
                # Re-identify ourselves over TLS connection.
                smtp_connection.ehlo()
            else:
                raise MailerException("SMTP server does not support STARTTLS")

        # If 'smtp.user' is in CKAN config, try to login to SMTP server.
        if smtp_user:
            assert smtp_password, ("If smtp.user is configured then "
                                   "smtp.password must be configured as well.")
            smtp_connection.login(smtp_user, smtp_password)

        '''
        Adding extra email fields and Sending notification for each individual member.
        '''

        for member in members:
            if member.email:
                body = email_dict['body']
                msg = MIMEText(body.encode('utf-8'), 'html', 'utf-8')
                msg['Subject'] = subject
                msg['From'] = "%s <%s>" % (sender_name, mail_from)
                msg['Date'] = utils.formatdate(time())
                recipient_email = member.email
                recipient_name = member.fullname or member.name
                body = add_msg_niceties(
                    recipient_name, body, sender_name, sender_url)
                recipient = "%s <%s>" % (recipient_name, recipient_email)
                msg['To'] = Header(recipient, 'utf-8')
                try:
                    smtp_connection.sendmail(
                        mail_from, [recipient_email], msg.as_string())
                    log.info("Sent state change email to user {0} with email {1}".format(
                        recipient_name, recipient_email))
                except Exception as e:
                    log.error('Failed to send notification to user {0} email address : {1}'.format(
                        recipient_email, recipient_email))

    except smtplib.SMTPException as e:
        msg = '%r' % e
        log.exception(msg)
        log.error('Failed to connect to smtp server')
    finally:
        smtp_connection.quit()


def check_record_state(context, old_state, new_data, site_title, site_url, dataset_url):
    '''
    Checks if the dataset state has been changed during the update and
    informs the users involving in package management.

    Updated by Khalegh Mamakani on MArch 5th 2015.

    List of changes :
        - replaced get_user_list with a model query to get the list of all members of the org with the given role
          ( Preventing action functions calls  and multiple for loops)
        - Removed the nested for loops for finding and sending notification to members (Replaced by a single for loop).
    '''

    new_state = new_data['publish_state']

    # If dataset's state has not been changed do nothing
    if (old_state == new_state):
        return

    '''
    Get the organization and sub-organization data
    '''
    org_id = new_data.get('owner_org')
    #sub_org_id = new_data.get('sub_org')

    org = model.Group.get(org_id)
    #sub_org = model.Group.get(sub_org_id)

    # Do not send emails for "DRAFT" datasets
    if new_state == "DRAFT":
        return

    # Basic dataset info
    dataset_title = new_data['title']
    org_title = org.title
    sub_org_title = ''#sub_org.title

    # Prepare email
    subject = ''
    msg_body = ''
    role = 'admin'

    # Change email based on new_state changes

    if new_state in ['REJECTED', 'PUBLISHED', 'ARCHIVED']:
        role = 'editor'

    if new_state in ['PENDING PUBLISH', 'REJECTED', 'PUBLISHED', 'PENDING ARCHIVE', 'ARCHIVED']:
        subject = 'BCDC - ' + new_state + ' ' + dataset_title

    # Prepare the dictionary for email content template

    msg_dict = dict(org=org_title, sub_org=sub_org_title, user_role=role,
                    dataset_url=dataset_url, dataset_state=new_state, prev_state=old_state, name=new_data['title'])
    msg_body = get_msg_content(msg_dict)

    email_dict = {'subject': subject, 'body': msg_body}

    # Get the entire list of users

    '''
    Get the list of sub-organization users with the given role; Added by Khalegh Mamakani
    '''

    log.info(
        'Sending state change notification to organization users with role %s' % (role,))

    query = model.Session.query(model.User) \
        .join(model.Member, model.User.id == model.Member.table_id) \
        .filter(model.Member.capacity == role) \
        .filter(model.Member.group_id == org.id) \
        .filter(model.Member.state == 'active') \
        .filter(model.User.state == 'active')

    members = query.all()

    send_state_change_notifications(members, email_dict, site_title, site_url)


def edc_package_update(context, input_data_dict):
    '''
    Find a package, from the given object_name, and update it with the given fields.
    1) Call __package_search to find the package
    2) Check the results (success == true), (count==1)
    3) Modify the data
    4) Call get_action(package_update) to update the package
    '''
    from ckan.lib.search import SearchError

    # first, do the search
    q = 'object_name:' + input_data_dict.get("object_name")
    fq = ''
    offset = 0
    limit = 2
    sort = 'metadata_modified desc'

    try:
        data_dict = {
            'q': q,
            'fq': fq,
            'start': offset,
            'rows': limit,
            'sort': sort
        }

        # Use package_search to filter the list
        query = get_action('package_search')(context, data_dict)

    except SearchError as se:
        log.error('Search error : %s', str(se))
        raise SearchError(str(se))

    # check the search results - there can be only 1!!
    results = query['results']
    the_count = query['count']
    if the_count != 1:
        log.error(
            'Search for the dataset with q={0} returned 0 or more than 1 record.'.format(q))

        return_dict = {}
        return_dict['results'] = query
        return_dict['success'] = False
        return_dict['error'] = True

    # results[0]['imap_layer_key'] = input_data_dict.get("imap_layer_key")
    # JER - the line below was removed because we don't use the data, and getting it into the query was a nightmare
    #
    # results[0]['imap_display_name'] = input_data_dict.get("imap_display_name")
    # results[0]['link_to_imap'] = input_data_dict.get("link_to_imap")

    try:
        package_dict = get_action('package_show')(
            context, {'id': results[0]['id']})

        if not package_dict:
            return_dict = {}
            return_dict['success'] = False
            return_dict['error'] = True
            return return_dict

        current_imap_link = package_dict.get('link_to_imap', None)
        visibility = package_dict['metadata_visibility']

        public_map_link = config.get('edc.imap_url_pub')
        private_map_link = config.get('edc.imap_url_gov')
        update = {}
        # don't update archived records

        # Upadted by Khalegh Mamakani to update the i-map link only if it has
        # not been done already.
        new_imap_link = None
        if (package_dict['publish_state'] != 'ARCHIVED'):
            if (visibility == 'Public'):
                if (input_data_dict.get("imap_layers_pub")):
                    new_imap_link = public_map_link + \
                        input_data_dict.get("imap_layers_pub")
            else:
                if (input_data_dict.get("imap_layers_gov")):
                    new_imap_link = private_map_link + \
                        input_data_dict.get("imap_layers_gov")
        if (new_imap_link is not None) and (new_imap_link is not current_imap_link):
            log.info('Updating IMAP Link to : {0} for dataset {1}'.format(
                new_imap_link, package_dict.get('title')))
            package_dict['link_to_imap'] = new_imap_link
            update = get_action('package_update')(context, package_dict)
    except Exception as ue:
        log.error('Error raised when updating dataset imap_link for dataset {0}.'.format(
            package_dict.get('name')))
        raise Exception(str(ue))

    response_dict = {}
    response_dict['results'] = update

    return response_dict


def edc_package_update_bcgw(context, input_data_dict):
    '''
    Find a package, from the given object_name, and update it with the given fields.
    1) Call __package_search to find the package
    2) Check the results (success == true), (count==1)
    3) Modify the data
    4) Call get_action(package_update) to update the package
    '''
    from ckan.lib.search import SearchError

    '''
    Fixed unicode characters decoding problem.
    '''
    input_dict_str = json.dumps(input_data_dict, ensure_ascii=False)
    input_data_dict = json.loads(input_dict_str, encoding="cp1252")

    update = {}
    # first, do the search
    q = 'object_name:' + input_data_dict.get("object_name")
    fq = ''
    offset = 0
    limit = 2
    sort = 'name asc'

    try:
        data_dict = {
            'query': q,
            'offset': offset,
            'limit': limit,
            'order_by': sort
        }

        # Use package_search to filter the list
        query = get_action('resource_search')(context, data_dict)
    except SearchError as se:
        log.error('Search error : %s', str(se))
        raise SearchError(str(se))

    # check the search results - there can be only 1!!
    the_count = query['count']
    if the_count != 1:
        # raise SearchError('Search returned 0 or more than 1 item')
        return_dict = {}
        return_dict['results'] = query
        return_dict['success'] = False
        return_dict['error'] = True
        return return_dict
    results = query['results']
    results[0]['details'] = input_data_dict.get("details")
    update = None

    # need the right data package
    resource_dict = get_action('resource_show')(
        context, {'id': results[0]['id']})

    # if package_dict['publish_state'] == 'ARCHIVED':
    #     return_dict = {}
    #     return_dict['results'] = None
    #     return return_dict

    if not resource_dict:
        return_dict = {}
        return_dict['success'] = False
        return_dict['error'] = True
        return return_dict

    # Check if input_data has been modified and is not the same as package data
    data_changed = False
    current_details = resource_dict.get('details')
    curent_obj_short_name = resource_dict.get('object_short_name')
    current_obj_table_comments = resource_dict.get('object_table_comments')
    new_details = json.dumps(input_data_dict.get('details'))

    if current_details != new_details:
        log.info('Resource details have been changed for resource {0}.'.format(
            resource_dict.get('name')))
        log.info('Current Details : ')
        log.info(current_details)
        log.info('New details :')
        log.info(input_data_dict.get('details'))

        resource_dict['details'] = new_details
        data_changed = True

    if curent_obj_short_name != input_data_dict.get('object_short_name'):
        log.info('Resource object_short_name has been changed for resource {0}.'.format(
            resource_dict.get('name')))
        log.info('Current object_short_name :')
        log.info(curent_obj_short_name)
        log.info('New object_short_name :')
        log.info(input_data_dict.get('object_short_name'))
        resource_dict['object_short_name'] = input_data_dict.get(
            'object_short_name')
        data_changed = True

    if current_obj_table_comments != input_data_dict.get('object_table_comments'):
        log.info('Resource current_obj_table_comments has been changed for resource {0}.'.format(
            resource_dict.get('name')))
        log.info('Current object_table_comments :')
        log.info(current_obj_table_comments)
        log.info('New object_table_comments :')
        log.info(input_data_dict.get('object_table_comments'))
        resource_dict['object_table_comments'] = input_data_dict.get(
            'object_table_comments')
        data_changed = True

    if data_changed:
        log.info('Updating data dictionary for resource {0}'.format(
            resource_dict.get('name')))

        update = get_action('resource_update')(context, resource_dict)

    response_dict = {}
    response_dict['results'] = update
    return response_dict

@toolkit.chained_action
def package_update(original_action, context, data_dict):
    log.debug('Updating package %s' % data_dict['name'])
    log.debug('Original action: %s' % original_action)
    log.debug('Context: %s' % context)
    log.debug('Data_dict:  %s' % data_dict)
    # Set the package last modified date
    data_dict['record_last_modified'] = str(datetime.date.today())

    # If the Created Date has not yet been set, then set it
    if data_dict['publish_state'] == 'DRAFT' and not data_dict.get('record_create_date'):
        data_dict['record_create_date'] = str(datetime.date.today())

    # If the Publish Date has not yet been set, then set it
    if data_dict['publish_state'] == 'PUBLISHED' and not data_dict.get('record_publish_date'):
        data_dict['record_publish_date'] = str(datetime.date.today())

    # If the Archive Date has not yet been set, then set it
    if data_dict['publish_state'] == 'ARCHIVED' and not data_dict.get('record_archive_date'):
        data_dict['record_archive_date'] = str(datetime.date.today())

    if data_dict['metadata_visibility'] == 'IDIR':
        data_dict['private'] = True;
    else:
        data_dict['private'] = False;

    '''
    Send state change notifications if required; Added by Khalegh Mamakani
    Using a thread to run the job in the background so that package_update will not wait for notifications sending.
    '''
    # old_data = get_action('package_show')(context, {'id': data_dict['id']})
    # old_state = old_data.get('publish_state')

    # dataset_url = config.get('ckan.site_url') + h.url_for('dataset.read', id=data_dict['id'])
    # import threading


    # notify_thread = threading.Thread(target=check_record_state, args=(
    #     context, old_state, data_dict, g.site_title, g.site_url, dataset_url))
    # notify_thread.start()
    log.debug('Calling original_action now?')
    return original_action(context, data_dict)


@toolkit.side_effect_free
def package_autocomplete(context, data_dict):
    '''Return a list of datasets (packages) that match a string.

    Datasets with names or titles that contain the query string will be
    returned.

    :param q: the string to search for
    :type q: string
    :param limit: the maximum number of resource formats to return (optional,
        default: 10)
    :type limit: int

    :rtype: list of dictionaries

    '''

    _check_access('package_autocomplete', context, data_dict)

    limit = data_dict.get('limit', 10)
    q = data_dict['q']

    q_lower = q.lower()
    pkg_list = []

    pkg_dict = get_action('package_search')(
        context, {'fq': 'title:' + q, 'rows': limit})

    pkg_dict = pkg_dict['results']
    for package in pkg_dict:
        if package['name'].startswith(q_lower):
            match_field = 'name'
            match_displayed = package['name']
        else:
            match_field = 'title'
            match_displayed = '%s (%s)' % (package['title'], package['name'])
        result_dict = {'name': package['name'], 'title': package['title'],
                       'match_field': match_field, 'match_displayed': match_displayed}
        pkg_list.append(result_dict)

    return pkg_list


@toolkit.side_effect_free
def tag_autocomplete_by_vocab(context, data_dict):
    tag_names = get_action('tag_autocomplete')(context, data_dict)

    resultSet = {
        'ResultSet': {
            'Result': [{'Name': tag} for tag in tag_names]
        }
    }
    return resultSet

@toolkit.side_effect_free
def member_list(context, data_dict):
    import ckan.authz as authz
    from ckan.logic.auth.create import _check_group_auth
    user_object = context['user']
    authorized = True

    #Do not authorize anonymous users
    if authz.auth_is_anon_user(context):
        authorized = False

    check2 = _check_group_auth(context,data_dict)
    if not check2:
        authorized = False

    if authorized:
        model = context['model']

        group = model.Group.get(_get_or_bust(data_dict, 'id'))
        if not group:
            raise NotFound

        obj_type = data_dict.get('object_type', None)
        capacity = data_dict.get('capacity', None)

        # User must be able to update the group to remove a member from it
        _check_access('group_show', context, data_dict)

        q = model.Session.query(model.Member).\
            filter(model.Member.group_id == group.id).\
            filter(model.Member.state == "active")

        if obj_type:
            q = q.filter(model.Member.table_name == obj_type)
        if capacity:
            q = q.filter(model.Member.capacity == capacity)

        trans = authz.roles_trans()

        def translated_capacity(capacity):
            try:
                return trans[capacity]
            except KeyError:
                return capacity

        return [(m.table_id, m.table_name, translated_capacity(m.capacity))
                for m in q.all()]

    return {"error": "Not found"}
 
    
def update_resource_refresh_timestamp(context, input_data_dict):
    '''
    Updates resource last_modified timstamp
    Parameters:
        id: resource_id
        timestamp: new timestamp for resource updated in format 2022-02-14 00:00:00
    '''
    log.debug('Params %s' % input_data_dict)
    try:
        resource_dict = get_action('resource_show')(context, {'id': input_data_dict.get("id")})
    except NotFound:
        raise NotFound(_('Resource was not found.'))

    if (not resource_dict['datastore_active']):
        resource_dict['last_modified'] = datetime.datetime.strptime(input_data_dict.get("timestamp"), "%Y-%m-%d %H:%M:%S")
        log.debug('Updated object %s' % resource_dict)
        get_action('resource_update')(context, resource_dict)
        resource_dict = get_action('resource_show')(context, {'id': input_data_dict.get("id")})

    return resource_dict
    
    
@toolkit.side_effect_free
def organization_or_group_list_related(context, data_dict):
    '''Return a list of the names of the site's organizations or groups.

    :param is_organization: return organizations list if true otherwise return groups list

    (default: ``False``)

    :type is_organization: boolean 

    :param all_fields: return full dictionaries instead of just names of organizations or groups

    (optional, default: ``False``) 

    :type all_fields: boolean 

    :rtype: list of strings'''

    model = context["model"]

    is_organization = (data_dict.get('is_organization', 'false')).lower() == 'true'
    query_result = {}

    if is_organization:
        query_result = model.Session.execute("""

        select g2.name as parent_org, g1.id, g1.name, g1.title,
               g1.title display_name, -- duplicating functionality of organization_show_related, but don't know why this field is needed
               g1.image_url, g1.is_organization, g1.description, 'organization' as type,
               case when g1.image_url <> '' then concat('/uploads/group/', g1.image_url) else '' end as image_display_url,
               g1.state, coalesce(url.value, '') as url

        from "group" g1

        left join member
        on member.group_id = g1.id and
           member.state = 'active' and
           member.table_name = 'group'

        left join "group" g2
        on member.table_id = g2.id and
           g2.is_organization = true and
           g2.state = 'active' and
           g2.approval_status = 'approved'

        left join "group_extra" url
        on g1.id = url.group_id and
           url.key = 'url' and
           url.state = 'active'

        where g1.is_organization = true and
              g1.state = 'active' and
              g1.approval_status = 'approved';

    """)
    else:
        query_result = model.Session.execute("""
            SELECT g1.id, g1.name, g1.title,
                   g1.title display_name, g1.image_url, g1.is_organization, g1.description,
                   CASE WHEN g1.image_url <> '' THEN CONCAT('/uploads/group/', g1.image_url) ELSE '' END AS image_display_url,
                   g1.state
            FROM "group" g1
            LEFT JOIN "group_extra" url ON g1.id = url.group_id AND
                                         url.key = 'url' AND
                                         url.state = 'active'
            WHERE g1.is_organization = FALSE AND
                  g1.state = 'active' AND
                  g1.approval_status = 'approved';
        """)

    model.Session.remove()

    # groups and oganizations are listed as groups in database
    all_groups = {}

    # gather all groups or organizations, add default values
    for grp in query_result:
        all_groups[grp.name] = dict(grp)
        
        if is_organization:
            all_groups[grp.name]["parent_of"] = []
            all_groups[grp.name]["child_of"] = []
        else:
            all_groups[grp.name]["type"] = "group"
        
        all_groups[grp.name]["package_count"] = 0


    # query for facets to get counts
    facet_field = "organization" if is_organization else "groups"
    package_search_results = toolkit.get_action("package_search")(
        context,
        { "q": "", "rows": "0", "facet.field": [facet_field]}) \
    .get("search_facets", {}) \
    .get(facet_field, {}) \
    .get("items", [])

   # associate base counts to all_groups list. Does not include aggregate counts
    # for parent orgs.
    for grp in package_search_results:
        if grp["name"] in all_groups:
            all_groups[grp["name"]]["package_count"] = grp["count"]

    # Add all the other fields that organization_show or group_show provides.
    # To optimize this further, could probably use inner joins in the
    # original SQL query, but that would mean lose the ability to use plugins to
    # mutate the result.
    # 
    # Most of the speed gain comes from disabling include_dataset_count, as that's
    # what triggers additional solr searches.
    if (data_dict.get('all_fields', 'false')).lower() == 'true':
        show_function = (
            toolkit.get_action("organization_show")
            if is_organization
            else toolkit.get_action("group_show")
        )
        for grp in all_groups.values():
            try:
                result = show_function(context, {
                    "id": grp["id"],
                    "include_datasets": False,
                    "include_dataset_count": False,
                    "include_tags": False,
                    "include_users": False,
                    "include_groups": False,
                    "include_extras": True,
                    "include_followers": False
                })
                for k, v in result.items():
                    grp[k] = v
            except Exception:
                pass

    # Set parent and children orgs & update counts
    if is_organization:
        for org_name, org in all_groups.items():
            if org["parent_org"]:
                parent = all_groups.get(org["parent_org"], None)
                if parent:
                    parent["parent_of"].append({ "title": org["title"], "name": org["name"] })
                    org["child_of"].append({ "title": parent["title"], "name": parent["name"] })
                    parents = [{ "name": parent["name"] }]
                    while parents:
                        o = parents.pop(0)
                        parents.extend(all_groups[o["name"]]["child_of"])
                        all_groups[o["name"]]["package_count"] += org["package_count"]

    return list(all_groups.values())
