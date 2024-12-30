# Copyright  2015, Province of British Columbia
# License: https://github.com/bcgov/ckanext-bcgov/blob/master/license
import datetime
import pytz
import pprint
import logging
import ckan.lib.helpers
import ckan.model as model
import ckan.plugins.toolkit as toolkit
import ckan.logic as logic
from ckan.common import  (c, request)
import json
import urllib.request, urllib.error, urllib.parse
import ckan.lib.base as base
from ckantoolkit import config

import ckanext.bcgov
from ckanext.bcgov.version import version
from ckanext.bcgov.util.git import get_short_commit_id

NotFound = logic.NotFound
get_action = logic.get_action
snippet = ckan.lib.helpers.snippet
url_for = ckan.lib.helpers.url_for

log = logging.getLogger('ckanext.bcgov')

abort = base.abort

from ckan.lib.helpers import unselected_facet_items

def get_suborgs(org_id):
    '''
    Returns the list of suborganizations of the given organization
    '''
    context = {'model': model, 'session':model.Session, 'user':c.user}

    org_model = context['model']

    group = org_model.Group.get(org_id)
    branches = group.get_children_groups(type="organization")

    suborgs = [branch.id for branch in branches]

    return suborgs

def get_org_parent(org_id):
    '''
    Returns the parent of an organization
    '''
    if not org_id:
        return False

    context = {'model': model, 'session':model.Session, 'user':c.user}
    org_model = context['model']

    group = org_model.Group.get(org_id)
    parent_org = group.get_parent_groups(type="organization")

    if parent_org and len(parent_org) > 0:
        return parent_org[0]
    else:
        return False

def get_suborg_sector(sub_org_id):
    '''
    Returns the sector that the sub_org with the input sub_org id belongs to.
    '''
    context = {'model': model, 'session':model.Session, 'user':c.user}

    data_dict = {
                 'id' : sub_org_id,
                 'include_datasets' : False
                 }

    sub_org = get_action('organization_show')(context, data_dict)
    return sub_org.get('sector')


def get_user_dataset_num(userobj):
    from ckan.lib.base import model
    from ckan.lib.search import SearchError
    from ckanext.bcgov.util.util import get_user_orgs, get_orgs_user_can_edit

    user_id = userobj.id

    #If this is the sysadmin user then return don't filter any dataset
    if userobj.sysadmin == True:
        fq = ''
    else :
        #Include only datsset created by this user or those from the orgs that the user has the admin role.
        fq = ' +(publish_state:("PUBLISHED" OR "PENDING ARCHIVE")'

        user_orgs = get_orgs_user_can_edit(userobj) #['"' + org + '"' for org in get_orgs_user_can_edit()]
        #user_orgs = ['"' + org.get('id') + '"' for org in get_user_orgs(user_id, 'admin')]
        #user_orgs += ['"' + org.get('id') + '"' for org in get_user_orgs(user_id, 'editor')]
        if len(user_orgs) > 0:
            fq += ' OR owner_org:(' + ' OR '.join(user_orgs) + ')'
        fq += ')'
    try:
        # package search
        context = {'model': model, 'session': model.Session,
                       'user': user_id}
        data_dict = {
                'q':'',
                'fq':fq,
                'facet':'false',
                'rows':0,
                'start':0,
        }
        query = toolkit.get_action('package_search')(context,data_dict)
        count = query['count']
    except SearchError as se:
        log.error('Search error: %s', se)
        count = 0

    return count

def get_index(seq, attr, value):
    return next(index for (index, d) in enumerate(seq) if d[attr] == value)

def record_is_viewable(pkg_dict, userobj):
    """
    Checks if the user is authorized to view the dataset.
    Public users can only see published or pending archive records and only if the metadata-visibility is public.
    Government users who are not admins or editors can only see the published or pending  archive records.
    Editors and admins can see all the records of their organizations in addition to what government users can see.
    """
    from ckanext.bcgov.util.util import get_orgs_user_can_edit  # , get_user_orgs

    # Anonymous user (visitor) can only view published public records
    published_state = ['PUBLISHED', 'PENDING ARCHIVE']

    if 'metadata_visibility' in pkg_dict:
        metadata_visibility = pkg_dict['metadata_visibility']
    else:
        metadata_visibility = get_package_extras_by_key('metadata_visibility', pkg_dict)

    if 'publish_state' in pkg_dict:
        publish_state = pkg_dict['publish_state']
    else:
        publish_state = get_package_extras_by_key('publish_state', pkg_dict)

    if metadata_visibility == 'Public' and publish_state in published_state:
        return True
    if userobj  :

        if metadata_visibility == 'IDIR' and publish_state in published_state:
            return True

        owner_org = False
        if 'owner_org' in pkg_dict:
            owner_org = pkg_dict['owner_org']
            
        user_orgs = get_orgs_user_can_edit(userobj)
        #user_orgs = [org.get('id') for org in get_user_orgs(userobj.id, 'editor') ]
        #user_orgs += [org.get('id') for org in get_user_orgs(userobj.id, 'admin') ]
        if owner_org in user_orgs:
            return True

        return False

    return False


def get_package_extras_by_key(pkg_extra_key, pkg_dict):
    '''
    Gets the specified `extras` field by pkg_extra_key, if it exists
    Returns False otherwise
    '''

    if 'extras' in pkg_dict:
        for extras in pkg_dict['extras']:
            if 'key' in extras:
                if extras['key'] == pkg_extra_key:
                    return extras['value']
        return False
    else:
        return False



def get_package_data(pkg_id):

    pkg_data = []
    context = {'model': model, 'session': model.Session,
               'user': c.user or c.author, 'auth_user_obj': c.userobj}

    try:
        pkg_data = toolkit.get_action('package_show')(context, {'id' : pkg_id})
    except NotFound:
        pass

    return pkg_data

def get_license_data(license_id):
    '''
    Returns the license information for the given license id.
    '''
    context = {'model': model, 'session': model.Session,
               'user': c.user, 'auth_user_obj': c.userobj}
    license_list = []
    try:
        license_list = toolkit.get_action('license_list')(context)
    except NotFound:
        pass

    #Check if liocense with the given id isin the licenses list
    for edc_license in license_list :
        if edc_license['id'] == license_id :
            return edc_license

    #License not found
    return None

def is_license_open(license_id):
    '''
    Checks if the license with given id is an open license.
    '''
    edc_license = get_license_data(license_id)

    if (edc_license and
        'is_open' in edc_license and
        edc_license['is_open'] == True):

        return True

    #License doesn't exist or it is not an open license
    return False

def get_record_type_label(rec_type):
    '''
    Used by search template as the label-function to show the proper dataset type.
    It maps the stored dataset type value to the more readable dataset type.
    '''
    type_dict = { 'Dataset' : 'Dataset', 'Geographic' : 'Geographic Dataset', 'Application' : 'Application', 'WebService' : 'Web Service / API'}

    if rec_type in type_dict :
        return type_dict[rec_type]
    return rec_type


def get_facets_unselected(facet, limit=None):
    '''Return the list of unselected facet items for the given facet, sorted
    by count.

    Reads the complete list of facet items for the given facet from
    c.search_facets, and filters out the facet items that the user has already
    selected.

    Arguments:
    facet -- the name of the facet to filter.
    limit -- the max. number of facet items to return.
    exclude_active -- only return unselected facets.

    '''
    if not c.search_facets or \
            not c.search_facets.get(facet) or \
            not c.search_facets.get(facet).get('items'):
        return []
    facets = []
    for facet_item in c.search_facets.get(facet)['items']:
        if not len(facet_item['name'].strip()):
            continue
        if not (facet, facet_item['name']) in list(request.params.items()):
            facets.append(dict(active=False, **facet_item))
    facets = sorted(facets, key=lambda item: item['count'], reverse=True)
    return facets

def get_facets_selected(facet):
    '''
    Returns the list of selected facet items for the given facet, sorted
    by count.
    '''
    if not c.search_facets or \
            not c.search_facets.get(facet) or \
            not c.search_facets.get(facet).get('items'):
        return []
    facets = []
    for facet_item in c.search_facets.get(facet)['items']:
        if not len(facet_item['name'].strip()):
            continue
        if (facet, facet_item['name']) in list(request.params.items()):
            facets.append(dict(active=False, **facet_item))
    facets = sorted(facets, key=lambda item: item['count'], reverse=True)
    return facets


_sectors_list = None

def get_sectors_list():
    '''
    Returns a list of sectors available in the file specified by sectors_file_url in ini file.
    The list of sectors are used when creating or editing a sub-organization
    in order to assign a new sector to the sub-organization.
    '''
    from ckan.plugins.toolkit import config
    global _sectors_list

    if _sectors_list is not None:
        return _sectors_list

    #Get the url for the sectors file.
    sectors_url = config.get('sectors_file_url', None)
    if sectors_url :
        try:
            response = urllib.request.urlopen(sectors_url)
            response_body = response.read()
        except Exception as inst:
            msg = "Couldn't access to sectors url %r: %s" % (sectors_url, inst)
            raise Exception(msg)
        try:
            sectors_data = json.loads(response_body)
        except Exception as inst:
            msg = "Couldn't read response from sectors %r: %s" % (response_body, inst)
            log.error("Couldn't read response from sectors %r: %s" % (response_body, inst))
            raise Exception(inst)
        sectors_list = sectors_data.get('sectors', [])
    else :
        '''
        Return the default sectors list
        '''
        sectors_list = ["Natural Resources", "Service", "Transportation", "Education", "Economy", "Social Services", "Health and Safety", "Justice", "Finance" ]

    _sectors_list = sectors_list
    return sectors_list


def get_dataset_type(id):
    '''
    Returns the dataset type for the dataset with given package id
    '''
    context = {'model': model, 'session': model.Session,
               'user': c.user or c.author, 'auth_user_obj': c.userobj}
    try:
        pkg_dict = get_action('package_show')(context, {'id': id})
        return pkg_dict['type']
    except NotFound:
        abort(404, _('The dataset {id} could not be found.').format(id=id))


def get_organizations():
    '''
    Returns the list of top-level organizations (Organizations that don't have any parent organizations).
    '''
    context = {'model': model, 'session': model.Session,
               'user': c.user or c.author, 'auth_user_obj': c.userobj}
    org_model = context['model']

    #Get the list of all groups of type "organization" that have no parents.
    top_level_orgs = org_model.Group.get_top_level_groups(type="organization")

    return top_level_orgs


def get_edc_org(org_id):
    return model.Group.get(org_id)
    # context = {'model': model, 'session': model.Session,
    #            'user': c.user or c.author, 'auth_user_obj': c.userobj}
    # try:
    #     org = get_action('organization_show')(context, {'id': org_id, 'include_datasets': False})
    # except NotFound:
    #     org = None
    # return org


def get_orgs_form(field = None):
    """Designed to get available orgs for scheming fields as parameters cannot be defined in choices_helper functions"""
    orgs = []
    for org in ckan.lib.helpers.organizations_available('view_dataset'):
        orgs.append({
            'value': org['id'],
            'label': org['display_name']
        })
    return orgs


def get_organization_title(org_id):
    '''
    Returns the title of an organization with the given organization id.
    '''
    org = get_edc_org(org_id)

    if org :
        return org.title
    return None


def get_espg_id(espg_string):
    import re
    a = re.compile("_([0-9]+)")
    espg_id = a.findall(espg_string)
    if len(espg_id)>0:
        return espg_id[0]
    else:
        return ""



def get_iso_topic_values(iso_topic_str):
    '''
    Converts the comma separated iso topic tokens into a list of iso topic values.
    '''
    iso_topic_values = []
    if iso_topic_str :
        iso_topic_values = [item.strip() for item in  iso_topic_str.split(',')]

    return iso_topic_values

def get_eas_login_url():
    '''Return the value of the eas login url config setting.

    '''
    value = config.get('edc.eas_url')

    return value

def get_fqdn():
    ''' Return the value of the edc_fdqn config setting '''

    value = config.get('edc.edc_fqdn')

    return value

def get_environment_name():
    ''' Return the value of the environment_name config setting '''
    # we seem to be using major_version for our environment name
    return config.get('edc.environment_name') or config.get('edc.major_version')

def get_version():
    ''' Return the value of the major_version config setting '''
    return version

_bcgov_commit_id = None

def get_bcgov_commit_id():
    '''Return the commit id corresponding to ckanext.bcgov'''
    global _bcgov_commit_id

    if _bcgov_commit_id is None:
        _bcgov_commit_id = get_short_commit_id(ckanext.bcgov.__path__[0])
        if _bcgov_commit_id is None:
            _bcgov_commit_id = 'unknown'

    return _bcgov_commit_id

def resource_prefix():
    return config.get('googleanalytics_resource_prefix', '/downloads/')

def size_or_link(upload, size):
    '''Returns a string with a localised filesize
    From ckan/lib/formatters.py localised_filesize()
    '''
    import ckan.lib.formatters as f

    if not size:
        return ''

    size = int(size)

    if upload and size > 0:
        return '(%s)' % f.localised_filesize(size)
    else:
        return ''


def display_pacific_time(date_str):
    utc_datetime = toolkit.h.date_str_to_datetime(date_str).replace(tzinfo=pytz.UTC)
    pacific = pytz.timezone('America/Vancouver')
    p_datetime = utc_datetime.astimezone(pacific)
    return p_datetime.strftime("%Y-%m-%d %H:%M:%S %Z")


def sort_vocab_list(vocab_list):
    '''
    This may change, but for now hardcoding the sort order to bring
    'EPSG_3005 - NAD83 BC Albers' to the top of the vocab list
    '''
    albers = 'EPSG_3005 - NAD83 BC Albers'
    try:
        _i = vocab_list.index(albers)
        vocab_list.insert(0, vocab_list.pop(_i))

    except ValueError as e:
        log.info(e)

    return vocab_list


def debug_full_info_as_list(debug_info):
    ''' This dumps the template variables for debugging purposes only. '''
    out = []
    ignored_keys = ['c', 'app_globals', 'g', 'h', 'request', 'tmpl_context',
                    'actions', 'translator', 'session', 'N_', 'ungettext',
                    'config', 'response', '_']
    ignored_context_keys = ['__class__', '__context', '__delattr__', '__dict__',
                            '__doc__', '__format__', '__getattr__',
                            '__getattribute__', '__hash__', '__init__',
                            '__module__', '__new__', '__reduce__',
                            '__reduce_ex__', '__repr__', '__setattr__',
                            '__sizeof__', '__str__', '__subclasshook__',
                            '__weakref__', 'action', 'environ', 'pylons',
                            'start_response']
    debug_vars = debug_info['vars']
    for key in list(debug_vars.keys()):
        if key not in ignored_keys:
            data = pprint.pformat(debug_vars.get(key))
            data = data.decode('utf-8')
            out.append((key, data))

    if 'tmpl_context' in debug_vars:
        for key in debug_info['c_vars']:

            if key not in ignored_context_keys:
                data = pprint.pformat(getattr(debug_vars['tmpl_context'], key))
                data = data.decode('utf-8')
                out.append(('c.%s' % key, data))

    return out


def get_namespace_config(namespace):
    return dict([(k.replace(namespace, ''), v) for k, v in config.items()
                 if k.startswith(namespace)])


def get_dashboard_config():
    '''
    Returns a dict with all configuration options related to the
    Dashboard (ie those starting with 'bcgov.dashboard.')
    '''
    return get_namespace_config('bcgov.dashboard.')


def get_ofi_config():
    '''
    Returns a dict with all configuration options related to the
    OFI (ie those starting with 'bcgov.ofi.')
    '''
    return get_namespace_config('bcgov.ofi.api.')


def get_pow_config():
    '''
    Return dict of all pow config options
    eg.
        bcgov.pow.env = delivery
        bcgov.pow.secure_site = false
        bcgov.pow.past_orders_nbr = 5
        bcgov.pow.custom_aoi_url = http://maps.gov.bc.ca/ess/hm/aoi/
        bcgov.pow.persist_config = true
        bcgov.pow.use_pow_ui = true
    '''
    return get_namespace_config('bcgov.pow.')


def _build_ofi_url(secure=False):
    ofi_config = get_ofi_config()

    if secure:
        url = ofi_config.get('secure_url', 'https://apps.gov.bc.ca/pub/dwds-ofi/secure')
    else:
        url = ofi_config.get('public_url', 'https://apps.gov.bc.ca/pub/dwds-ofi')

    log.debug('OFI base API URL: %s', url)
    return url


def get_non_ofi_resources(pkg):
    return [i for i in pkg['resources'] if 'ofi' not in i or i['ofi'] is False]


def get_ofi_resources(pkg):
    return [i for i in pkg['resources'] if 'ofi' in i and i['ofi']]


def log_this(this):
    try:
        log.info(this)
    except:
        return False
