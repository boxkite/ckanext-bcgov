# Copyright  2015, Province of British Columbia 
# License: https://github.com/bcgov/ckanext-bcgov/blob/master/license 
 
import urllib.request, urllib.error, urllib.parse
import json
import logging

import ckan.model as model
import ckan.plugins as p
from ckan.common import c

import ckan.logic as logic
import ckan.lib.datapreview as datapreview

from ckanext.bcgov.util.helpers import get_record_type_label
from sqlalchemy import and_, distinct
from sqlalchemy.orm import aliased

toolkit = p.toolkit
ValidationError = logic.ValidationError

from ckantoolkit import config

site_url = config.get('ckan.site_url')
api_key = config.get('ckan.api_key')
log = logging.getLogger('ckanext.bcgov.util')

MAX_FILE_SIZE = config.get('ckan.resource_proxy.max_file_size', 1024**2)

def edc_type_label(item):
    rec_type = item['display_name']
    return get_record_type_label(rec_type)

def add_admin(member, site_url, api_key):
    ''' adds a user as admin of an organization'''

    data_string = urllib.parse.quote(json.dumps(member))

    member_dict = None
    errors = None
    try:
        request = urllib.request.Request(site_url + '/api/3/action/organization_member_create')
        request.add_header('Authorization', api_key)
        response = urllib.request.urlopen(request, data_string)
        assert response.code == 200

        # Use the json module to load CKAN's response into a dictionary.
        response_dict = json.loads(response.read())
        if response_dict['success'] == True :
            member_dict = response_dict
        else:
            errors = response_dict['error']
    except Exception as e:
        print(str(e))
        pass
    return (member_dict, errors)

def get_edc_tags(vocab_id):
    '''
    Returns the list of tags for a given vocabulary.

    Issue came up with sorting, there is no control mechanism for sorting
    tag lists, however in the future if an issue as such arises again
    can use this snippet of code to match the list ordering from edc_vocab.json

    sorted(vocab_list_from_db, key=lambda x: edc_json_vocab_list.index(x))
    '''
    tags = []
    try:
        tags = toolkit.get_action('tag_list')(
                data_dict={'vocabulary_id': vocab_id})
    except toolkit.ObjectNotFound:
        return []

    return tags

def get_username(id):
    '''
    Returns the user name for the given id.
    '''
    
    try:
        user = toolkit.get_action('user_show')(data_dict={'id': id})
        return user['name']
    except toolkit.ObjectNotFound:
        return None

def get_orgs_user_can_edit(userobj) :
    '''
    Returns the list of id's of organizations that the current logged in user
    can edit. The user must have an admin or editor role in the organization.
    '''

    if not userobj :
        return []

    orgs = []

    perm_dict = {'permission' : 'create_dataset'}
    orgs = toolkit.get_action('organization_list_for_user')(data_dict=perm_dict)

    orgs = [org['id'] for org in orgs]
    
    return orgs

def get_user_toporgs(user_id, role=None):

    '''
    Optimized by Khalegh Mamakani to use SqlAlchemy queries
    to get the user top-orgs/sub-orgs instead of looping over
    ckan member_list action which, could result in very long response times.

    '''

    orgs = []
    sub_orgs = []


    user_mem = aliased(model.Member);
    group_mem = aliased(model.Member);

    # Query to get the list of all top organizations
    top_orgs_query = model.Session.query(model.Group)\
                        .join(model.Member, 
                            and_(model.Member.table_id == model.Group.id,
                                model.Member.table_name == 'group',
                                model.Member.state == 'active'
                            )
                        )


    # Query for the list of all sub-organizations
    sub_orgs_query = model.Session.query(model.Group)\
                        .join(model.Member, 
                            and_(model.Member.group_id == model.Group.id,
                                model.Member.table_name == 'group',
                                model.Member.state == 'active'
                            )
                        )

    if not c.userobj.sysadmin :
        # Restricting the top-orgs query to get user's top organizations 
        top_orgs_query = top_orgs_query.join(user_mem, 
                            and_(user_mem.group_id == model.Group.id,
                                user_mem.table_name == 'user',
                                user_mem.state == 'active',
                                user_mem.table_id == user_id
                            )
                        )\
                        .filter(model.Group.type == 'organization')\
                        .filter(model.Group.state == 'active')

        # Including all remaining top-organizations that user is a member of at least one of their sub-organizations
        top_2nd_q = model.Session.query(model.Group)\
                        .join(group_mem, 
                            and_(group_mem.table_id == model.Group.id,
                                group_mem.table_name == 'group',
                                group_mem.state == 'active'
                            )
                        )\
                        .join(user_mem, 
                            and_(user_mem.group_id == group_mem.group_id,
                                user_mem.table_name == 'user',
                                user_mem.state == 'active',
                                user_mem.table_id == user_id
                            )
                        )\
                        .filter(model.Group.type == 'organization')\
                        .filter(model.Group.state == 'active')

        # Restritcting sub-orgs query to get the list of user's sub-organizations.
        sub_orgs_query = sub_orgs_query.join(user_mem, 
                            and_(user_mem.group_id == model.Group.id,
                                user_mem.table_name == 'user',
                                user_mem.state == 'active',
                                user_mem.table_id == user_id
                            )
                        )\
                        .filter(model.Group.type == 'organization')\
                        .filter(model.Group.state == 'active')

        # Including all sub-organiztions that user is member of their top-organization
        sub_2nd_q = model.Session.query(model.Group)\
                        .join(group_mem, 
                            and_(group_mem.group_id == model.Group.id,
                                group_mem.table_name == 'group',
                                group_mem.state == 'active'
                            )
                        )\
                        .join(user_mem, 
                            and_(user_mem.group_id == group_mem.table_id,
                                user_mem.table_name == 'user',
                                user_mem.state == 'active',
                                user_mem.table_id == user_id
                            )
                        )\
                        .filter(model.Group.type == 'organization')\
                        .filter(model.Group.state == 'active')

        # Filtering the query results by user role.
        if role :
            top_orgs_query = top_orgs_query.filter(user_mem.capacity == role)
            top_2nd_q = top_2nd_q.filter(user_mem.capacity == role)
            sub_orgs_query = sub_orgs_query.filter(user_mem.capacity == role)
            sub_2nd_q = sub_2nd_q.filter(user_mem.capacity == role)

        top_orgs_query = top_orgs_query.union(top_2nd_q)
        sub_orgs_query = sub_orgs_query.union(sub_2nd_q)

    else :
        top_orgs_query = top_orgs_query.filter(model.Group.type == 'organization')\
                                        .filter(model.Group.state == 'active')

        sub_orgs_query = sub_orgs_query.filter(model.Group.type == 'organization')\
                                        .filter(model.Group.state == 'active')


    orgs = top_orgs_query.distinct()
    sub_orgs = sub_orgs_query.distinct()


    return (orgs, sub_orgs)

def get_user_orgs(user_id, role=None):
    '''
    Returns the list of orgs and suborgs that the given user belongs to and has the given role('admin', 'member', 'editor', ...)
    '''
                
    '''
    Optimized by Khalegh Mamakani to use SqlAlchemy queries
    to get the user organizations instead of looping over
    ckan's member_list action. This makes a huge saving in response time
    for non-sysadmin users.     
    '''

    
    user_mem = aliased(model.Member);
    group_mem = aliased(model.Member);

    member_query = model.Session.query(model.Member.group_id.label('id')) \
                   .filter(model.Member.table_name == 'user') \
                   .filter(model.Member.state == 'active') \
                   .filter(model.Member.table_id == user_id) \
                   .filter(model.Member.capacity == role)

    
    
    '''
    This second query is required only if we want to add all sub-organizations that
    user is not a member, but he/she is a member of the top-level orgnization. This has
    been an assumption decided by bcgov but it must be removed if we want to get only 
    the list of orgs/sub-orgs that the user has the given role in the org/sub-org in database. 
    '''

    
    mem_2nd_q = model.Session.query(model.Group.id)\
                    .join(group_mem, 
                        and_(group_mem.group_id == model.Group.id,
                            group_mem.table_name == 'group',
                            group_mem.state == 'active'
                        )
                    )\
                    .join(user_mem, 
                        and_(user_mem.group_id == group_mem.table_id,
                            user_mem.table_name == 'user',
                            user_mem.state == 'active',
                            user_mem.table_id == user_id
                        )
                    )\
                    .filter(model.Group.type == 'organization')\
                    .filter(model.Group.state == 'active')

    # Filtering the query results by user role.
    if role :
        mem_2nd_q = mem_2nd_q.filter(user_mem.capacity == role)

    member_query = member_query.union(mem_2nd_q)
    
    org_ids = member_query.distinct();

    orgs_dict = [org.__dict__ for org in org_ids.all()]

    '''
    user_orgs =  c.userobj.get_group_ids('organization', role)   


    pprint.pprint(orgs_dict)

    pprint.pprint(user_orgs)
    '''

    return orgs_dict
    


    '''    
    orgs = []
    context = {'model': model, 'session': model.Session,
               'user': c.user or c.author, 'auth_user_obj': c.userobj}

    perm_role = {'admin' : 'admin', 'editor' : 'create_dataset', 'member' : 'read'}
    permission = perm_role.get(role)
    perm_dict = {'permission' : permission}
    orgs = toolkit.get_action('organization_list_for_user')(data_dict=perm_dict)

    return orgs
    '''

def get_user_orgs_id(user_id, role=None):
    user_orgs = get_user_orgs(user_id, role)
    return [org.id for org in user_orgs]

def get_organization_branches(org_id):
    '''
    Get the list of branches for the organization with the given id.
    '''    
    context = {'model': model, 'session': model.Session,
               'user': c.user or c.author, 'auth_user_obj': c.userobj}
    org_model = context['model']

    #Get the list of all children of the organization with the given id
    group = org_model.Group.get(org_id)
    branches = group.get_children_groups(type = 'organization')

    #should only return branches the user is a member of
    return branches
    
def get_parent_orgs(org_id):
    '''
    returns the parent organization(s) of the organization with the given id.
    '''
    context = {'model': model, 'session': model.Session,
               'user': c.user or c.author, 'auth_user_obj': c.userobj}
    org_model = context['model']

    #Get the list of all parents of the organization with the given id
    group = org_model.Group.get(org_id)
    parents = group.get_parent_groups(type = 'organization')

    
    return parents    

def get_org_admins(org_id):
    '''
    Returns the list of admins of the given organization
    '''
    try:
        member_dict = {'id': org_id, 'object_type': 'user', 'capacity': 'admin'}
        #Get the list of id's of all admins of the organization
        members = [member[0] for member in toolkit.get_action('member_list')(data_dict=member_dict)]
    except toolkit.ObjectNotFound:
        members = []
    return members

def get_org_users(org_id, role=None):
    '''
    Returns the list of admins of the given organization
    '''
    try:
        member_dict = {'id': org_id, 'object_type': 'user', 'capacity': role}
        #Get the list of id's of all admins of the organization
        members = [member[0] for member in toolkit.get_action('member_list')(data_dict=member_dict)]
    except toolkit.ObjectNotFound:
        members = []
    return members

def get_state_values(userobj, pkg):
    '''
    This methods creates a list of possible values for the state of given dataset
    based on the current state of dataset and the user that updates the dataset.
    :param user_id: The id of the current user.
    :param pkg : The given dataset.
    :returns: A list of possible values of the state of the dataset.
    '''
    states = []


    if not pkg or 'id' not in pkg:
        return ['DRAFT']

    id = pkg['id']
    org_id = pkg['owner_org']


    #Get the list of admins of the dataset
    member_data = {
                   'id' : org_id,
                   'object_type' : 'user',
                   'capacity' : 'admin'
                   }

    if org_id :
        admins = [admin[0] for admin in toolkit.get_action('member_list')(data_dict=member_data)]
    else :
        admins = []

    current_state = pkg['publish_state']

    if current_state == 'DRAFT':
        states = ['DRAFT', 'PENDING PUBLISH']
    elif current_state == 'REJECTED' :
        states = ['DRAFT', 'PENDING PUBLISH', 'REJECTED']
    elif current_state == 'PENDING PUBLISH':
        if userobj.id in admins or userobj.sysadmin :
            states = ['PENDING PUBLISH', 'PUBLISHED', 'REJECTED']
        else :
            states = ['DRAFT', 'PENDING PUBLISH']
    elif current_state == 'PUBLISHED' :
        if userobj.id in admins or userobj.sysadmin :
            states = ['PENDING PUBLISH', 'PUBLISHED', 'PENDING ARCHIVE']
        else :
            states = ['PUBLISHED', 'PENDING ARCHIVE']
    elif current_state == 'PENDING ARCHIVE' :
        if userobj.id in admins or userobj.sysadmin :
            states = ['PUBLISHED', 'PENDING ARCHIVE', 'ARCHIVED']
        else :
            states = ['PUBLISHED', 'PENDING ARCHIVE']
    elif current_state == 'ARCHIVED' :
        if userobj.id in admins or userobj.sysadmin:
            states = ['PENDING ARCHIVE', 'ARCHIVED']
        else :
            states = ['ARCHIVED']

    return states

def get_all_orgs():
    '''
    Returns all user orgs as a dictionary with the id as the key
    and the value is a tuple of (name, title)
    '''
    orgs_dict = {}

    all_orgs = model.Group.all('organization')
    
    for org in all_orgs:
        orgs_dict[org['id']] = {'name': org.name, 'title': org.title }

    return orgs_dict

def can_view_resource(resource):
    '''
    Returns a boolean if the resource is able to be loaded / viewed. 
    This is intended to add functionality beyond user view permissions.

    return True if the resource is viewable, False if not.
    '''
    data_dict = { 'resource':resource }
    format_lower = resource.get('format', '').lower()

    proxy_enabled = p.plugin_loaded('resource_proxy')
    same_domain = datapreview.on_same_domain(data_dict)

    '''
        requirement
        - If the format is a PDF (uses pdfview), from a proxy thats smaller than the configuration proxy max size value.
    '''
    if format_lower in ['pdf', 'x-pdf', 'acrobat', 'vnd.pdf']:
        try:
            usock = urllib.request.urlopen(resource['url'], '')
            if same_domain:
                return same_domain
            elif proxy_enabled:    
                size =  usock.info().get('Content-Length')
                if size is None:
                    size = 0
                size = float(size) # in bytes
                
                if size > MAX_FILE_SIZE:
                    return False
                else:
                    return True
        except Exception as e:
            log.error('can_view_resource:')
            log.error(e)
            return True

    return False

def get_package_tracking(package_id):
    return ({'views':model.TrackingSummary.get_for_package(package_id), 'downloads':None})
    
def get_resource_tracking(resource_url, resource_id):
    download_resource_id = config.get('ckan.datasource.downloads.resource')
    downloads = 0
    
    try:
        request = urllib.request.Request(site_url + '/api/3/action/datastore_search')
        data_string = urllib.parse.quote(json.dumps({'resource_id':download_resource_id,'q':resource_id}))
        request.add_header('Authorization', api_key)

        response = urllib.request.urlopen(request, data_string)
        assert response.code == 200

        # Use the json module to load CKAN's response into a dictionary.
        response_dict = json.loads(response.read())
        assert response_dict['success'] is True
        downloads = response_dict['result']['total']

    except Exception as e:
        log.error('get_resource_tracking:')
        log.error(e)
        pass

    return ({'views':model.TrackingSummary.get_for_resource(resource_url), 'downloads':downloads})


def can_access_group(group_id):
    try:
        result = logic.check_access("group_show", { "user": c.user }, { "id": group_id })

        # Unlike with the check_access function, which we want to permit 404s,
        # we have to return false for groups that don't exist, as otherwise
        # group name changes could reveal groups in search facets.

        # This raises a NotFound error if the group cannot be located.
        group = toolkit.get_action('group_show')(
            { 'ignore_auth': True },
            { 'id': group_id })

        return True

    except logic.NotAuthorized:
        return False

    except logic.NotFound:
        return False

def convert_composite_fields_to_array(resource):
    compositeResourceFields = ['temporal_extent', 'details', 'preview_info', 'geographic_extent']
    log.debug('Print resource data-/n %s', resource)
    for field in compositeResourceFields:
        if (field in resource) and isinstance(resource[field], str):
            log.debug('%s is in resource', field)
            log.debug('Print field data- %s', resource[field])
            try:
                resource[field] = json.loads(resource[field])
            except:
                resource[field] = []