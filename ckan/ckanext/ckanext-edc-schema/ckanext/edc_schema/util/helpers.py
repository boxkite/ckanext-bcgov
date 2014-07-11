import logging
import ckan.lib.helpers

import ckan.model as model
import ckan.plugins.toolkit as toolkit
import ckan.logic as logic
from ckan.common import  c

NotFound = logic.NotFound
snippet = ckan.lib.helpers.snippet
url_for = ckan.lib.helpers.url_for
log = logging.getLogger(__name__)


def get_suborg_sectors(org, suborg):
    from ckanext.edc_schema.commands.base import default_org_file
    import os
    import json
    import sys
    
    sectors = []
    
    org_file = default_org_file
              
    if not os.path.exists(org_file):
        print 'File {0} does not exists'.format(org_file)
        sys.exit(1)
                                 
    #Read the organizations json file
    with open(org_file) as json_file:
        orgs = json.loads(json_file.read())
    
    branches = []                  
    #Create each organization and all of its branches if it is not in the list of available organizations
    for org_item in orgs:
        
        if org_item['title'] == org:
            branches = org_item['branches']
            break
    
    if branches != [] :                                             
        for branch in branches:
            if branch['title'] == suborg and 'sectors' in branch :
                sectors = branch["sectors"]
                break
    return sectors


def get_user_dataset_num(userobj):
    from ckan.lib.base import model
    from ckan.lib.search import SearchError
    from ckanext.edc_schema.util.util import get_user_orgs
    
    user_id = userobj.id
    
    #If this is the sysadmin user then return don't filter any dataset
    if userobj.sysadmin == True:
        fq = ''
    else :
        #Include only datsset created by this user or those from the orgs that the user has the admin role.
        fq = 'author:("%s")' %(user_id) 
        user_orgs = ['"' + org.id + '"' for org in get_user_orgs(user_id, 'admin')]
        if len(user_orgs) > 0:
            fq += ' OR owner_org:(' + ' OR '.join(user_orgs) + ')'
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
    except SearchError, se:
        log.error('Search error: %s', se)
        count = 0

    return count


def record_is_viewable(pkg_dict, userobj=None, user='visitor'):
    '''
    Checks if the user can view the given record
    '''
    
    from ckanext.edc_schema.util.util import get_user_orgs
    
    #Sysadmin can view all records
    if userobj and userobj.sysadmin == True :
        return True
    
    #Anonymous user (visitor) can only view published public records
    published_state = ['PUBLISHED', 'PENDING ARCHIVE', 'ARCHIVED']
    if user == 'visitor' :
        if pkg_dict['metadata_visibility'] == '002' and pkg_dict['edc_state'] in published_state:
            return True
        else :
            return False
        
    #Users can only view unpublished records of their own organization:
    if userobj :
        if pkg_dict['edc_state'] in published_state or pkg_dict['owner_org'] in get_user_orgs(user) :
            return True
        else :
            return False
    
def get_package_data(pkg_id):
    '''
    Returns the list of orgs that the given user belongs to and has the given role('admin', 'member', 'editor', ...)
    '''
    pkg_data = []
    context = {'model': model, 'session': model.Session,
               'user': c.user or c.author, 'auth_user_obj': c.userobj}    
    
    try:
        pkg_data = toolkit.get_action('package_show')(context, {'id' : pkg_id})
    except NotFound:
        pass
    
    return pkg_data

def get_license_data(license_id):
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
    
    edc_license = get_license_data(license_id)
    
    if edc_license and edc_license['is_open'] == True :
        return True
    
    #License doesn't exist or it is not an open license    
    return False
            
def get_record_type_label(rec_type):
    type_dict = { 'Dataset' : 'Dataset', 'Geographic' : 'Geographic Dataset', 'Application' : 'Application', 'WebService' : 'Web Service / API'}
    
    if rec_type in type_dict : 
        return type_dict[rec_type]
    return rec_type