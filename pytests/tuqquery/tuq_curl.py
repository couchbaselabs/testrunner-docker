import logging
import threading
import json
import uuid
import time

from tuq import QueryTests
from membase.api.rest_client import RestConnection
from membase.api.exception import CBQError, ReadDocumentException
from remote.remote_util import RemoteMachineShellConnection
from security.rbac_base import RbacBase

SOURCE_CB_PARAMS = {
                      "authUser": "default",
                      "authPassword": "",
                      "authSaslUser": "",
                      "authSaslPassword": "",
                      "clusterManagerBackoffFactor": 0,
                      "clusterManagerSleepInitMS": 0,
                      "clusterManagerSleepMaxMS": 20000,
                      "dataManagerBackoffFactor": 0,
                      "dataManagerSleepInitMS": 0,
                      "dataManagerSleepMaxMS": 20000,
                      "feedBufferSizeBytes": 0,
                      "feedBufferAckThreshold": 0
                    }
INDEX_DEFINITION = {
                          "type": "fulltext-index",
                          "name": "",
                          "uuid": "",
                          "params": {},
                          "sourceType": "couchbase",
                          "sourceName": "default",
                          "sourceUUID": "",
                          "sourceParams": SOURCE_CB_PARAMS,
                          "planParams": {}
                        }

class QueryCurlTests(QueryTests):
    def setUp(self):
        super(QueryCurlTests, self).setUp()
        self.shell = RemoteMachineShellConnection(self.master)
        self.info = self.shell.extract_remote_info()
        if self.info.type.lower() == 'windows':
            self.curl_path = "%scurl" % self.path
        else:
            self.curl_path = "curl"
        self.rest = RestConnection(self.master)
        self.cbqpath = '%scbq' % self.path + " -u %s -p %s" % (self.rest.username,self.rest.password)
        self.query_service_url = "'http://%s:%s/query/service'" % (self.master.ip,self.n1ql_port)
        self.api_port = self.input.param("api_port", 8094)
        self.load_sample = self.input.param("load_sample", False)
        self.create_users = self.input.param("create_users", False)
        self.run_cbq_query('delete from system:prepareds')

    def suite_setUp(self):
        super(QueryCurlTests, self).suite_setUp()
        if self.load_sample:
            self.rest.load_sample("beer-sample")
            index_definition = INDEX_DEFINITION
            index_name = index_definition['name'] = "beers"
            index_definition['sourceName'] = "beer-sample"
            index_definition['sourceParams']['authUser'] = "beer-sample"
            rest_src_fts = RestConnection(self.servers[0])
            try:
                status, _ = rest_src_fts.get_fts_index_definition(index_name)
                if status != 400:
                    rest_src_fts.delete_fts_index(index_name)
                rest_src_fts.create_fts_index(index_name, index_definition)
            except Exception, ex:
                self.fail(ex)
        # Create the users necessary for the RBAC tests in curl
        if self.create_users:
            testuser = [{'id': 'no_curl', 'name': 'no_curl', 'password': 'password'},
                        {'id': 'curl', 'name': 'curl', 'password': 'password'},
                        {'id': 'curl_no_insert', 'name': 'curl_no_insert', 'password': 'password'}]
            RbacBase().create_user_source(testuser, 'builtin', self.master)

            noncurl_permissions = 'bucket_full_access[*]:query_select[*]:query_update[*]:' \
                                  'query_insert[*]:query_delete[*]:query_manage_index[*]:' \
                                  'query_system_catalog'
            curl_permissions = 'bucket_full_access[*]:query_select[*]:query_update[*]:' \
                               'query_insert[*]:query_delete[*]:query_manage_index[*]:' \
                               'query_system_catalog:query_external_access'
            curl_noinsert_permissions = 'query_select[*]:query_manage_index[*]' \
                                        ':query_system_catalog:query_external_access'
            # Assign user to role
            role_list = [{'id': 'no_curl', 'name': 'no_curl','roles': '%s' % noncurl_permissions},
                         {'id': 'curl', 'name': 'curl', 'roles': '%s' % curl_permissions},
                          {'id': 'curl_no_insert', 'name': 'curl_no_insert',
                           'roles': '%s' % curl_noinsert_permissions}]
            temp = RbacBase().add_user_role(role_list, self.rest, 'builtin')

    def tearDown(self):
        super(QueryCurlTests, self).tearDown()

    def suite_tearDown(self):
        super(QueryCurlTests, self).suite_tearDown()

    '''Basic test for using POST in curl'''
    def test_POST(self):
        # The query that curl will send to couchbase
        n1ql_query = 'select * from default limit 5'
        # This is the query that the cbq-engine will execute
        query = "select curl("+ self.query_service_url +\
                ", {'data' : 'statement=%s','user':'%s:%s'})" % (n1ql_query,self.username,self.password)
        curl = self.shell.execute_commands_inside(self.cbqpath,query,'', '', '', '', '')
        json_curl = self.convert_to_json(curl)
        # Compare the curl statement to the expected result of the n1ql query done normally
        expected_result = self.run_cbq_query('select * from default limit 5')
        self.assertTrue(json_curl['results'][0]['$1']['results'] == expected_result['results'])

    '''Basic test for using GET in curl'''
    def test_GET(self):
        url = "'http://%s:%s/pools/default/buckets/default'" % (self.master.ip,self.master.port)
        query = "select curl("+ url +",{'user':'%s:%s'})"  % (self.username,self.password)
        curl = self.shell.execute_commands_inside(self.cbqpath,query,'', '', '', '', '')
        json_curl = self.convert_to_json(curl)
        self.assertTrue(json_curl['results'][0]['$1']['name'] == 'default')

    '''Basic test for having curl in the from clause'''
    def test_from(self):
        n1ql_query = 'select * from default limit 5'
        select_query = "select *"
        from_query=" from curl("+ self.query_service_url +\
                   ", {'data' : 'statement=%s','user':'%s:%s'}) result" % (n1ql_query,self.username,self.password)
        curl = self.shell.execute_commands_inside(self.cbqpath,select_query + from_query,'', '', '', '', '')
        json_curl = self.convert_to_json(curl)
        expected_result = self.run_cbq_query('select * from default limit 5')
        self.assertTrue(json_curl['results'][0]['result']['results'] == expected_result['results'])

    '''Basic Test that tests if curl works inside the where clause of a query'''
    def test_where(self):
        n1ql_query = 'select raw d from default d limit 5&metrics=false&signature=false'
        select_query = "select * "
        from_query="from default d "
        where_query="where curl("+ self.query_service_url +\
                    ", {'data' : 'statement=%s','user':'%s:%s'}).results[0].name == 'employee-9' " % (n1ql_query,self.username,self.password)
        curl = self.shell.execute_commands_inside(self.cbqpath,select_query+from_query+where_query,'', '', '', '', '')
        json_curl = self.convert_to_json(curl)
        # This should be equiv to select * from default d where true, so all docs should be present, assuming doc-per-day = 1
        self.assertTrue(json_curl['metrics']['resultCount'] == 2016*self.docs_per_day)

        where_query="where curl("+ self.query_service_url \
                    +", {'data' : 'statement=%s','user':'%s:%s'}).results[0].name == 'Ajay' " % (n1ql_query,self.username,self.password)
        curl = self.shell.execute_commands_inside(self.cbqpath,select_query+from_query+where_query,'', '', '', '', '')
        json_curl = self.convert_to_json(curl)
        # This should be equiv to select * from default d where false, so no docs should be present
        self.assertTrue(json_curl['metrics']['resultCount'] == 0)

    '''Basic test for having curl in the select and from clause at the same time'''
    def test_select_and_from(self):
        n1ql_query = 'select * from default limit 5'
        from_n1ql_query= 'select * from default'
        select_query = "select curl(" + self.query_service_url + ", {'data' : 'statement=%s','user':'%s:%s'})" % (n1ql_query,self.username,self.password)
        from_query = " from curl(" + self.query_service_url + ", {'data' : 'statement=%s','user':'%s:%s'}) result" % (from_n1ql_query,self.username,self.password)
        curl = self.shell.execute_commands_inside(self.cbqpath, select_query + from_query, '', '', '', '', '')
        json_curl = self.convert_to_json(curl)
        expected_result = self.run_cbq_query('select * from default limit 5')
        self.assertTrue(json_curl['results'][0]['$1']['results'] == expected_result['results'])

    '''Basic test for having curl in the from and where clause at the same time'''
    def test_from_and_where(self):
        n1ql_query = 'select d from default d limit 5'
        where_n1ql_query = 'select raw d from default d limit 5&metrics=false&signature=false'
        select_query = "select * "
        from_query="from curl(" + self.query_service_url + ", {'data' : 'statement=%s','user':'%s:%s'}) result " % (n1ql_query,self.username,self.password)
        where_query="where curl("+ self.query_service_url +\
                    ", {'data' : 'statement=%s','user':'%s:%s'}).results[0].name == 'employee-9' " % (where_n1ql_query,self.username,self.password)
        curl = self.shell.execute_commands_inside(self.cbqpath,select_query+from_query+where_query,'', '', '', '', '')
        json_curl = self.convert_to_json(curl)
        self.assertTrue(json_curl['metrics']['resultCount'] == 1)

        where_query = "where curl(" + self.query_service_url + \
                      ", {'data' : 'statement=%s','user':'%s:%s'}).results[0].name == 'Ajay' " % (where_n1ql_query,self.username,self.password)
        curl = self.shell.execute_commands_inside(self.cbqpath,select_query+from_query+where_query,'', '', '', '', '')
        json_curl = self.convert_to_json(curl)
        self.assertTrue(json_curl['metrics']['resultCount'] == 0)

    '''Basic test that tests if curl works while inside a subquery'''
    def test_curl_subquery(self):
        n1ql_query = 'select * from default limit 5'
        select_query = "select * "
        from_query="from default d "
        where_query="where d.name in (select raw result.results[0].default.name  from curl(" + self.query_service_url + ", {'data' : 'statement=%s','user':'%s:%s'}) result) " % (n1ql_query,self.username,self.password)
        curl = self.shell.execute_commands_inside(self.cbqpath,select_query+from_query+where_query,'', '', '', '', '')
        json_curl = self.convert_to_json(curl)
        expected_result = self.run_cbq_query("select * from default d where d.name == 'employee-9'")
        self.assertTrue(json_curl['results'] == expected_result['results'])

    '''Test if you can join using curl as one of the buckets to be joined'''
    def test_curl_join(self):
        url = "'http://%s:%s/api/index/beers/query'" % (self.master.ip,self.api_port)
        select_query = "select ARRAY x.id for x in b1.result.hits end as hits, b1.result.total_hits as total," \
                       "array_length(b1.result.hits), b "
        from_query = "from (select curl("+ url +", "
        options = "{'headers':'Content-Type: application/json'," \
                  "'data': '{\\\"explain\\\":true,\\\"fields\\\": [\\\"*\\\"],\\\"highlight\\\": {},\\\"query\\\": {\\\"query\\\":\\\"garden\\\"}}','user':'%s:%s' }) result)b1 " % (self.username,self.password)
        join = "INNER JOIN \`beer-sample\` b ON KEYS b1.result.hits[*].id"
        curl = self.shell.execute_commands_inside(self.cbqpath,select_query+from_query+options+join,'', '', '', '', '')
        json_curl = self.convert_to_json(curl)
        self.assertTrue(json_curl['metrics']['resultCount'] == 10 and json_curl['results'][0]['$1'] == 10 and
                        len(json_curl['results'][0]['hits']) == 10)

    '''Test that you can insert data from a select curl statement into couchbase
        -inserting data pulled from another bucket
        -inserting data with a key that already exists (should fail)'''
    def test_insert_curl(self):
        n1ql_query = 'select * from \`beer-sample\` limit 1'
        insert_query ="insert into default (key UUID(), value curl_result.results[0].\`beer-sample\`) "
        query = "select curl("+ self.query_service_url +", "
        options = "{'data' : 'statement=%s','user': '%s:%s'}) curl_result " % (n1ql_query,self.username,self.password)
        returning ="returning meta().id, * "
        curl = self.shell.execute_commands_inside(self.cbqpath,insert_query+query+options+returning,'', '', '','', '')
        json_curl = self.convert_to_json(curl)
        docid = json_curl['results'][0]['id']
        result = self.run_cbq_query('select * from default limit 1')
        result2= self.run_cbq_query('select * from `beer-sample` limit 1')
        self.assertTrue(result['results'][0]['default'] == result2['results'][0]['beer-sample']
                        and json_curl['metrics']['mutationCount'] == 1)

        insert_query = "insert into default (key '" + docid+"', value curl_result.results[0].\`beer-sample\`) "
        curl = self.shell.execute_commands_inside(self.cbqpath,insert_query+query+options+returning,'', '', '','', '')
        json_curl = self.convert_to_json(curl)
        self.assertTrue(json_curl['status'] == 'errors' and 'cause:DuplicateKey' in json_curl['errors'][0]['msg'])

        curl_query = 'select meta().id from default limit 1'
        result = self.run_cbq_query(curl_query)
        docid = result['results'][0]['id']
        delete_query ="delete from default d use keys '" + docid + "'"
        result = self.run_cbq_query(delete_query)

    '''Test that you can insert data from a select curl statement using upsert
        -inserting data pulled from another bucket
        -insertign data using a key that already exists(should work)'''
    def test_upsert_curl(self):
        n1ql_query = 'select * from \`beer-sample\` limit 1'
        insert_query ="upsert into default (key UUID(), value curl_result.results[0].\`beer-sample\`) "
        query = "select curl("+ self.query_service_url +", "
        options = "{'data' : 'statement=%s','user': '%s:%s'}) curl_result " % (n1ql_query,self.username,self.password)
        returning ="returning meta().id, * "
        curl = self.shell.execute_commands_inside(self.cbqpath,insert_query+query+options+returning,'', '', '','', '')
        json_curl = self.convert_to_json(curl)
        docid = json_curl['results'][0]['id']
        result = self.run_cbq_query('select * from default limit 1')
        result2= self.run_cbq_query('select * from `beer-sample` limit 1')
        self.assertTrue(result['results'][0]['default'] == result2['results'][0]['beer-sample'])

        n1ql_query = 'select * from \`beer-sample\` offset 1 limit 1'
        insert_query = "upsert into default (key '" + docid+"', value curl_result.results[0].\`beer-sample\`) "
        options = "{'data' : 'statement=%s','user': '%s:%s'}) curl_result " % (n1ql_query,self.username,self.password)
        curl = self.shell.execute_commands_inside(self.cbqpath,insert_query+query+options+returning,'', '', '','', '')
        json_curl = self.convert_to_json(curl)
        result = self.run_cbq_query('select * from default limit 1')
        result2= self.run_cbq_query('select * from `beer-sample` offset 1 limit 1')
        self.assertTrue(result['results'][0]['default'] == result2['results'][0]['beer-sample'])

    '''See if you can update a bucket using n1ql curl'''
    def test_update_curl(self):
        query = 'select meta().id from default limit 1'
        n1ql_query = "select d.join_yr from default d where d.join_yr == 2010 limit 1"
        result = self.run_cbq_query(query)
        docid = result['results'][0]['id']
        update_query ="update default use keys '" + docid + "' set name ="
        query = "(curl("+ self.query_service_url +", "
        options = "{'data' : 'statement=%s','user': '%s:%s'}).results[0].join_yr) " % (n1ql_query,self.username,self.password)
        returning ="returning meta().id, * "
        curl = self.shell.execute_commands_inside(self.cbqpath,update_query+query+options+returning,'', '', '','', '')
        json_curl = self.convert_to_json(curl)
        self.assertTrue(json_curl['results'][0]['default']['name'] == 2010)

    '''Test if curl can be used inside a delete'''
    def test_delete_curl(self):
        curl_query = 'select meta().id from default limit 1'
        result = self.run_cbq_query(curl_query)
        docid = result['results'][0]['id']
        delete_query ="delete from default d use keys "
        query = "(curl("+ self.query_service_url +", "
        options = "{'data' : 'statement=%s','user': '%s:%s'}).results[0].id) " % (curl_query,self.username,self.password)
        returning ="returning meta().id, * "
        curl = self.shell.execute_commands_inside(self.cbqpath,delete_query+query+options+returning,'', '', '','', '')
        json_curl = self.convert_to_json(curl)
        result = self.run_cbq_query('select * from default')
        self.assertTrue(json_curl['metrics']['mutationCount'] == 1 and json_curl['results'][0]['id'] == docid and
                        result['metrics']['resultCount'] == ((2016 * self.docs_per_day) - 1))

    '''Test if you can access a protected bucket with curl and authorization'''
    def test_protected_bucket(self):
        n1ql_query = 'select * from bucket0 limit 5'
        query = "select curl("+ self.query_service_url +", "
        options = "{'data' : 'statement=%s', 'user': 'bucket0:password'})" % n1ql_query
        curl = self.shell.execute_commands_inside(self.cbqpath,query+options,'', '', '', '', '')
        json_curl = self.convert_to_json(curl)
        self.assertTrue(json_curl['results'][0]['$1']['metrics']['resultCount'] == 5)

    '''Test a n1ql curl query containing the use of FTS'''
    def test_basic_fts_curl(self):
        url = "'http://%s:%s/api/index/beers/query'" % (self.master.ip,self.api_port)
        select_query = "select result.total_hits, array_length(result.hits) "
        from_query = "from curl("+ url +", "
        options = "{'header':'Content-Type: application/json', " \
                  "'data': '{\\\"explain\\\":true,\\\"fields\\\": [\\\"*\\\"],\\\"highlight\\\": {},\\\"query\\\": {\\\"query\\\":\\\"garden\\\"}}','user':'%s:%s' }) result" % (self.username,self.password)
        curl = self.shell.execute_commands_inside(self.cbqpath,select_query+from_query+options,'', '', '', '', '')
        json_curl = self.convert_to_json(curl)
        self.assertTrue(json_curl['results'][0]['total_hits'] == 12 and json_curl['results'][0]['$1'] == 10)

##############################################################################################
#
#   External endpoint Tests
#
##############################################################################################

    '''Test basic requests to json endpoints outside of couchbase
        -https://jsonplaceholder.typicode.com/todos
        -https://jsonplaceholder.typicode.com/users (contains more complex data than above
        -http://data.colorado.gov/resource/4ykn-tg5h.json/ (is a website with real data)'''
    def test_simple_external_json(self):
        # Get the output from the actual curl and test it against the n1ql curl query
        curl_output = self.shell.execute_command("%s https://jsonplaceholder.typicode.com/todos"
                                                 % self.curl_path)
        # The above command returns a tuple, we want the first element of that tuple
        expected_curl = self.convert_list_to_json(curl_output[0])

        url = "'https://jsonplaceholder.typicode.com/todos'"
        query = "select curl("+ url + ")"
        curl = self.shell.execute_commands_inside(self.cbqpath,query,'', '', '', '', '')
        # Convert the output of the above command to json
        actual_curl = self.convert_to_json(curl)
        self.assertTrue(actual_curl['results'][0]['$1'] == expected_curl)

        # Test for more complex data
        curl_output = self.shell.execute_command("%s https://jsonplaceholder.typicode.com/users"
                                                 % self.curl_path)
        expected_curl = self.convert_list_to_json(curl_output[0])
        url = "'https://jsonplaceholder.typicode.com/users'"
        query = "select curl("+ url + ")"
        curl = self.shell.execute_commands_inside(self.cbqpath,query,'', '', '', '', '')
        actual_curl = self.convert_to_json(curl)
        self.assertTrue(actual_curl['results'][0]['$1'] == expected_curl)

        # Test for a website in production (the website above is only used to provide json endpoints with fake data)
        curl_output = self.shell.execute_command("%s http://data.colorado.gov/resource/4ykn-tg5h.json/"
                                                 %self.curl_path)
        expected_curl = self.convert_list_to_json(curl_output[0])
        url = "'http://data.colorado.gov/resource/4ykn-tg5h.json/'"
        query = "select curl("+ url + ")"
        curl = self.shell.execute_commands_inside(self.cbqpath,query,'', '', '', '', '')
        actual_curl = self.convert_to_json(curl)
        self.assertTrue(actual_curl['results'][0]['$1'] == expected_curl)

    '''Test external endpoints in a the from field of a query
        -select * from curl result
        -select result.username from curl result
        -select * from curl result where result.username == 'Bret'.'''
    def test_from_external(self):
        url = "'https://jsonplaceholder.typicode.com/users'"
        select_query = "select *"
        from_query=" from curl("+ url +") result "
        curl = self.shell.execute_commands_inside(self.cbqpath,select_query + from_query,'', '', '', '', '')
        # Convert the output of the above command to json
        json_curl = self.convert_to_json(curl)
        self.assertTrue(json_curl['metrics']['resultCount'] == 10)

        # Test the use of curl in the from as a bucket, see if you can specify only usernames
        select_query = "select result.username"
        curl = self.shell.execute_commands_inside(self.cbqpath,select_query + from_query,'', '', '', '', '')
        json_curl = self.convert_to_json(curl)
        # Need to make sure that only the usernames were stored, that correlates to a resultsize of 478
        self.assertTrue(json_curl['metrics']['resultSize'] == 478)

        # Test of the use of curl in the from as a bucket, see if you can filter results
        select_query = "select *"
        where_query ="where result.username == 'Bret'"
        curl = self.shell.execute_commands_inside(self.cbqpath,select_query + from_query+ where_query,'', '', '', '', '')
        json_curl = self.convert_to_json(curl)
        self.assertTrue(json_curl['metrics']['resultCount'] == 1 and
                        json_curl['results'][0]['result']['username'] == 'Bret')

    '''Test request to the google maps api with an api key'''
    def test_external_json_google_api_key(self):
        # Test the google maps json endpoint with a valid api key and make sure it works
        curl_output = self.shell.execute_command("%s --get https://maps.googleapis.com/maps/api/geocode/json "
                                                 "-d 'address=santa+cruz&components=country:ES&key=AIzaSyCT6niGCMsgegJkQSYSqpoLZ4_rSO59XQQ'"
                                                 % self.curl_path)
        expected_curl = self.convert_list_to_json(curl_output[0])
        url = "'https://maps.googleapis.com/maps/api/geocode/json'"
        options= "{'get':True,'data': 'address=santa+cruz&components=country:ES&key=AIzaSyCT6niGCMsgegJkQSYSqpoLZ4_rSO59XQQ'}"
        query="select curl("+ url +", %s" % options + ")"
        curl = self.shell.execute_commands_inside(self.cbqpath,query,'', '', '', '', '')
        actual_curl = self.convert_to_json(curl)
        self.assertTrue(actual_curl['results'][0]['$1'] == expected_curl)

    '''Test request to a JIRA json endpoint'''
    def test_external_json_jira(self):
        curl_output = self.shell.execute_command("%s https://jira.atlassian.com/rest/api/latest/issue/JRA-9"
                                                 %self.curl_path)
        expected_curl = self.convert_list_to_json(curl_output[0])
        url = "'https://jira.atlassian.com/rest/api/latest/issue/JRA-9'"
        query="select curl("+ url +")"
        curl = self.shell.execute_commands_inside(self.cbqpath,query,'', '', '', '', '')
        actual_curl = self.convert_to_json(curl)
        self.assertTrue(actual_curl['results'][0]['$1'] == expected_curl)

    '''MB-22128 giving a header without giving data would cause an empty result set'''
    def test_external_json_jira_with_header(self):
        curl_output = self.shell.execute_command("%s https://jira.atlassian.com/rest/api/latest/issue/JRA-9"
                                                 %self.curl_path)
        expected_curl = self.convert_list_to_json(curl_output[0])

        url = "'https://jira.atlassian.com/rest/api/latest/issue/JRA-9'"
        query="select * from curl("+ url +") result"
        curl = self.shell.execute_commands_inside(self.cbqpath,query,'', '', '', '', '')
        actual_curl = self.convert_to_json(curl)
        self.assertTrue(actual_curl['results'][0]['result'] == expected_curl)

        query="select * from curl("+ url +",{'header':'Content-Type: application/json'}) result"
        curl = self.shell.execute_commands_inside(self.cbqpath,query,'', '', '', '', '')
        actual_curl = self.convert_to_json(curl)
        self.assertTrue(actual_curl['results'][0]['result'] == expected_curl)

    '''MB-22291 Test to make sure that endpoints returning an array of JSON docs work, also tests the user-agent option'''
    def test_array_of_json(self):
        curl_output = self.shell.execute_command("%s https://api.github.com/users/ikandaswamy/repos"
                                                 %self.curl_path)
        expected_curl = self.convert_list_to_json(curl_output[0])
        url = "'https://api.github.com/users/ikandaswamy/repos'"
        query="select raw curl("+ url +",{'header':'User-Agent: ikandaswamy'}) list"
        curl = self.shell.execute_commands_inside(self.cbqpath,query,'', '', '', '', '')
        actual_curl = self.convert_to_json(curl)
        self.assertTrue(actual_curl['results'][0] == expected_curl)

        query="select raw curl("+ url +",{'user-agent':'ikandaswamy'}) list"
        curl = self.shell.execute_commands_inside(self.cbqpath,query,'', '', '', '', '')
        actual_curl = self.convert_to_json(curl)
        self.assertTrue(actual_curl['results'][0] == expected_curl)

    '''Test that redirect links are not followed, queries to links that contain redirects should
       return null'''
    def test_redirect(self):
        url = "'https://httpbin.org/redirect-to?url=" \
              "https://jira.atlassian.com/rest/api/latest/issue/JRA-9'"
        query="select curl("+ url +")"
        curl = self.shell.execute_commands_inside(self.cbqpath,query,'', '', '', '', '')
        actual_curl = self.convert_to_json(curl)
        self.assertTrue(actual_curl['results'][0]['$1'] == None)

        url = "'https://httpbin.org/redirect-to?url=" \
              "file:///opt/couchbase/var/lib/couchbase/config/ssl-cert-key.pem'"
        query="select curl("+ url +")"
        curl = self.shell.execute_commands_inside(self.cbqpath,query,'', '', '', '', '')
        actual_curl = self.convert_to_json(curl)
        self.assertTrue(actual_curl['results'][0]['$1'] == None)

        # Regular curl only follows redirect if the location option is used, make sure location
        # option cannot be used by n1ql curl
        unsupported_error='Errorevaluatingprojection.-cause:CURLoptionlocationisnotsupported.'
        url = "'https://httpbin.org/redirect-to?url=" \
              "file:///opt/couchbase/var/lib/couchbase/config/ssl-cert-key.pem'"
        query="select curl("+ url +",{'location':True})"
        curl = self.shell.execute_commands_inside(self.cbqpath,query,'', '', '', '', '')
        actual_curl = self.convert_to_json(curl)
        self.assertTrue(actual_curl['errors'][0]['msg'] == unsupported_error)

    '''Test curl being used inside a prepared statement
        -prepare a statement that uses curl
        -use curl to prepare a statement'''
    def test_curl_prepared(self):
        curl_output = self.shell.execute_command("%s --get https://maps.googleapis.com/maps/api/geocode/json -d 'address=santa+cruz&components=country:ES&key=AIzaSyCT6niGCMsgegJkQSYSqpoLZ4_rSO59XQQ'"
                                                 % self.curl_path)
        expected_curl = self.convert_list_to_json_with_spacing(curl_output[0])
        url = "'https://maps.googleapis.com/maps/api/geocode/json'"
        options= "{'get':True,'data': 'address=santa+cruz&components=country:ES&key=AIzaSyCT6niGCMsgegJkQSYSqpoLZ4_rSO59XQQ'}"
        query="PREPARE curl_query FROM select curl("+ url +", %s" % options + ")"
        self.run_cbq_query(query)
        result = self.run_cbq_query('select * from system:prepareds')
        self.assertTrue(result['results'][0]['prepareds']['uses'] == 0)
        actual_curl = self.run_cbq_query(query='curl_query', is_prepared=True)
        self.assertTrue(actual_curl['results'][0]['$1'] == expected_curl)
        result = self.run_cbq_query('select * from system:prepareds')
        self.assertTrue(result['results'][0]['prepareds']['uses'] == 1)

        self.run_cbq_query('delete from system:prepareds')
        n1ql_query = 'prepare prepared_with_curl FROM select * from default limit 5'
        # This is the query that the cbq-engine will execute
        query = "select curl("+ self.query_service_url +", {'data' : 'statement=%s','user':'%s:%s'})" % (n1ql_query,self.username,self.password)
        curl = self.shell.execute_commands_inside(self.cbqpath,query,'', '', '', '', '')
        json_curl = self.convert_to_json(curl)
        result = self.run_cbq_query('select * from system:prepareds')
        self.assertTrue(result['results'][0]['prepareds']['name'] == 'prepared_with_curl' and result['metrics']['resultCount'] == 1
                        and result['results'][0]['prepareds']['statement'] == n1ql_query )

    '''Check if the cipher_list returned by pinging the website is consistent with the expected list
       of ciphers'''
    def test_cipher_list(self):
        cipher_list = ['TLS_RSA_WITH_AES_128_CBC_SHA', 'TLS_RSA_WITH_AES_256_CBC_SHA',
                       'TLS_ECDHE_ECDSA_WITH_AES_256_CBC_SHA', 'TLS_ECDHE_RSA_WITH_AES_256_CBC_SHA',
                       'TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256', 'TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256',
                       'TLS_EMPTY_RENEGOTIATION_INFO_SCSV']
        url = "'https://www.howsmyssl.com/a/check'"
        query = "select curl(" + url + ")"
        curl = self.shell.execute_commands_inside(self.cbqpath, query, '', '', '', '', '')
        actual_curl = self.convert_to_json(curl)
        self.assertTrue(actual_curl['results'][0]['$1']['given_cipher_suites'] == cipher_list)

##############################################################################################
#
#   Options Tests
#
##############################################################################################
    '''WIP'''
    def test_connect_timeout(self):
        # Test with a connectiion time that is too small for the request to go through
        n1ql_query = 'select * from default'
        select_query = "select curl(" + self.query_service_url + ", {'data' : 'statement=%s', 'user':'%s:%s','connect-timeout':1})" % (n1ql_query,self.username,self.password)
        curl = self.shell.execute_commands_inside(self.cbqpath, select_query, '', '', '', '', '')
        json_curl = self.convert_to_json(curl)
        import pdb;pdb.set_trace()

        # Test with a connection time that is big enough for the request to go through
        n1ql_query = 'select * from default'
        select_query = "select curl(" + self.query_service_url + ", {'data' : 'statement=%s', 'user':'%s:%s','connect-timeout':15})" % (n1ql_query,self.username,self.password)
        curl = self.shell.execute_commands_inside(self.cbqpath, select_query, '', '', '', '', '')
        json_curl = self.convert_to_json(curl)

    '''Test the option that supresses output
        -Test the option with a curl call that should return output
        -Test the option with a curl call that should return an error
        -Test the option being off'''
    def test_silent(self):
        n1ql_query = 'select * from default limit 5'
        select_query = "select curl(" + self.query_service_url + ", {'data' : 'statement=%s', 'user':'%s:%s','silent':True})" % (n1ql_query,self.username,self.password)
        curl = self.shell.execute_commands_inside(self.cbqpath, select_query, '', '', '', '', '')
        silent_curl = self.convert_to_json(curl)
        self.assertTrue(silent_curl['results'][0]['$1'] == None)

        select_query = "select curl(" + self.query_service_url + ", {'data' : 'statement=%s', 'user':'%s:','silent':True})" % (n1ql_query,self.username)
        curl = self.shell.execute_commands_inside(self.cbqpath, select_query, '', '', '', '', '')
        silent_curl = self.convert_to_json(curl)
        self.assertTrue(silent_curl['results'][0]['$1'] == None)

        select_query = "select curl(" + self.query_service_url + ", {'data' : 'statement=%s', 'user':'%s:%s','silent':False})" % (n1ql_query,self.username,self.password)
        curl = self.shell.execute_commands_inside(self.cbqpath, select_query, '', '', '', '', '')
        json_curl = self.convert_to_json(curl)
        result = self.run_cbq_query(n1ql_query)
        self.assertTrue(json_curl['results'][0]['$1']['results'] == result['results'])

    '''Test the max-time option for transferring data
        -Test with a max-time that will result in a timeout
        -Test with a max-time that will result in a successful transfer of data'''
    def test_max_time(self):
        n1ql_query = 'select * from default'
        select_query = "select curl(" + self.query_service_url + ", {'data' : 'statement=%s', 'user':'%s:%s','max-time':1})" % (n1ql_query,self.username,self.password)
        curl = self.shell.execute_commands_inside(self.cbqpath, select_query, '', '', '', '', '')
        json_curl = self.convert_to_json(curl)
        self.assertTrue(json_curl['errors'][0]['msg'] == 'Errorevaluatingprojection.-cause:curl:Timeoutwasreached')

        select_query = "select curl(" + self.query_service_url + ", {'data' : 'statement=%s', 'user':'%s:%s','max-time':5})" % (n1ql_query,self.username,self.password)
        curl = self.shell.execute_commands_inside(self.cbqpath, select_query, '', '', '', '', '')
        json_curl = self.convert_to_json(curl)
        self.assertTrue(json_curl['results'][0]['$1']['metrics']['resultCount'] == 2016 * self.docs_per_day)

    '''Tests what happens when you give two of the same option with different values.'''
    def test_repeated_user_options(self):
        error_message = 'Userdoesnothavecredentialstoaccessprivilegecluster.bucket[default].n1ql.' \
                        'select!execute.AddroleQuerySelect[default]toallowthequerytorun.'
        n1ql_query = 'select * from default limit 5'
        select_query = "select curl(" + self.query_service_url + ", {'data' : 'statement=%s', 'user':'%s:%s','user': 'Ajay:Bhullar'})" % (n1ql_query,self.username,self.password)
        curl = self.shell.execute_commands_inside(self.cbqpath, select_query, '', '', '', '', '')
        json_curl = self.convert_to_json(curl)
        self.assertTrue(json_curl['results'][0]['$1']['errors'][0]['msg'] == error_message)

        select_query = "select curl(" + self.query_service_url + ", {'data' : 'statement=%s', 'user': 'Ajay:Bhullar', 'user':'%s:%s'})" % (n1ql_query,self.username,self.password)
        curl = self.shell.execute_commands_inside(self.cbqpath, select_query, '', '', '', '', '')
        json_curl = self.convert_to_json(curl)
        self.assertTrue(json_curl['results'][0]['$1']['metrics']['resultCount'] == 5)

    '''Tests the different ways to set the user-agent'''
    def test_user_agent(self):
        url = "'httpbin.org/user-agent'"
        options = "{'headers': 'User-Agent:ikandaswamy','user-agent':'ajay'}"
        query = "select curl(" + url +", " + options + ")"
        curl = self.shell.execute_commands_inside(self.cbqpath,query,'','','','','')
        json_curl = self.convert_to_json(curl)
        result = self.run_cbq_query(query)
        self.assertTrue(result['results'][0]['$1']['user-agent'] == "ikandaswamy"
                        and json_curl['results'][0]['$1']['user-agent'] == "ikandaswamy")

        options = "{'headers': ['User-Agent:ikandaswamy','User-Agent:ajay']}"
        query = "select curl(" + url + ", " + options + ")"
        curl = self.shell.execute_commands_inside(self.cbqpath, query, '', '', '', '', '')
        json_curl = self.convert_to_json(curl)
        result = self.run_cbq_query(query)
        self.assertTrue(result['results'][0]['$1']['user-agent'] == "ikandaswamy,ajay"
                        and json_curl['results'][0]['$1']['user-agent'] == "ikandaswamy,ajay")

    '''Tests the different ways to specify which method to use'''
    def test_conflicting_method_options(self):
        limit = 5
        n1ql_query = 'select * from default limit %s' % limit
        invalid_json_error = 'Errorevaluatingprojection.-cause:InvalidJSONendpointhttp:' \
                             '//%s:%s/query/service'%(self.master.ip,self.n1ql_port)

        options = "{'data' : 'statement=%s', 'user':'%s:%s'}" \
                  % (n1ql_query, self.username, self.password)
        select_query = "select curl(" + self.query_service_url + ", " + options +")"
        curl = self.shell.execute_commands_inside(self.cbqpath, select_query, '', '', '', '', '')
        json_curl = self.convert_to_json(curl)
        self.assertTrue(json_curl['results'][0]['$1']['metrics']['resultCount'] == limit)

        options = "{'data' : 'statement=%s', 'user':'%s:%s','request':'POST'}" \
                  % (n1ql_query, self.username, self.password)
        select_query = "select curl(" + self.query_service_url + ", " + options +")"
        curl = self.shell.execute_commands_inside(self.cbqpath, select_query, '', '', '', '', '')
        json_curl = self.convert_to_json(curl)
        self.assertTrue(json_curl['results'][0]['$1']['metrics']['resultCount'] == limit)

        options = "{'data' : 'statement=%s', 'user':'%s:%s','request':'POST','get':True}" \
                  % (n1ql_query, self.username, self.password)
        select_query = "select curl(" + self.query_service_url + ", " + options +")"
        curl = self.shell.execute_commands_inside(self.cbqpath, select_query, '', '', '', '', '')
        json_curl = self.convert_to_json(curl)
        self.assertTrue( json_curl['errors'][0]['msg'] == invalid_json_error)

        options = "{'data' : 'statement=%s', 'user':'%s:%s','get':True,'request':'POST'}" \
                  % (n1ql_query, self.username, self.password)
        select_query = "select curl(" + self.query_service_url + ", " + options +")"
        curl = self.shell.execute_commands_inside(self.cbqpath, select_query, '', '', '', '', '')
        json_curl = self.convert_to_json(curl)
        self.assertTrue(json_curl['errors'][0]['msg'] == invalid_json_error)

    def test_conflicting_get_options(self):
        curl_output = self.shell.execute_command(
            "%s --get https://maps.googleapis.com/maps/api/geocode/json -d "
            "'address=santa+cruz&components=country:ES&key"
            "=AIzaSyCT6niGCMsgegJkQSYSqpoLZ4_rSO59XQQ'" % self.curl_path)
        expected_curl = self.convert_list_to_json(curl_output[0])
        url = "'https://maps.googleapis.com/maps/api/geocode/json'"
        options = "{'get':False,'request':'GET','data': " \
                  "'address=santa+cruz&components=country:ES&key" \
                  "=AIzaSyCT6niGCMsgegJkQSYSqpoLZ4_rSO59XQQ'}"
        query = "select curl(" + url + ", %s" % options + ")"
        curl = self.shell.execute_commands_inside(self.cbqpath, query, '', '', '', '', '')
        actual_curl = self.convert_to_json(curl)
        self.assertTrue(actual_curl['results'][0]['$1'] == expected_curl)

        options = "{'request':'GET','get':False,'data': " \
                  "'address=santa+cruz&components=country:ES&key" \
                  "=AIzaSyCT6niGCMsgegJkQSYSqpoLZ4_rSO59XQQ'}"
        query = "select curl(" + url + ", %s" % options + ")"
        curl = self.shell.execute_commands_inside(self.cbqpath, query, '', '', '', '', '')
        actual_curl = self.convert_to_json(curl)
        self.assertTrue(actual_curl['results'][0]['$1'] == expected_curl)


    '''MB-23132 - make sure max-redirs isnt recognized as a valid option, and that another option that isn't supported
       throws an error.'''
    def test_unsupported_option(self):
        n1ql_query = 'select * from default'
        select_query = "select curl(" + self.query_service_url + ", {'data' : 'statement=%s', 'user':'%s:%s','max-redirs': 5})" % (n1ql_query,self.username,self.password)
        curl = self.shell.execute_commands_inside(self.cbqpath, select_query, '', '', '', '', '')
        json_curl = self.convert_to_json(curl)
        self.assertTrue(json_curl['errors'][0]['msg'] == 'Errorevaluatingprojection.-cause:CURLoptionmax-redirsisnotsupported.')

        select_query = "select curl(" + self.query_service_url + ", {'data' : 'statement=%s', 'user':'%s:%s','fake_option': 200})" % (n1ql_query,self.username,self.password)
        curl = self.shell.execute_commands_inside(self.cbqpath, select_query, '', '', '', '', '')
        json_curl = self.convert_to_json(curl)
        self.assertTrue(json_curl['errors'][0]['msg'] == 'Errorevaluatingprojection.-cause:CURLoptionfake_optionisnotsupported.')

##############################################################################################
#
#   Negative tests
#
##############################################################################################

    '''Tests what happens when curl receives a protocol that isn't valid
        -misspelled protocol
        -unsupported protocol (MB-23134)'''
    def test_invalid_protocol(self):
        # Test invalid protocol (misspelled)
        url = "'htpps://maps.googleapis.com/maps/api/geocode/json'"
        query="select curl("+ url +")"
        curl = self.shell.execute_commands_inside(self.cbqpath,query,'', '', '', '', '')
        actual_curl = self.convert_to_json(curl)
        self.assertTrue(actual_curl['errors'][0]['msg'] =='Errorevaluatingprojection.-cause:curl:Unsupportedprotocol')

        # Test unsupported protocol file
        protocol = "'file:///Users/isha/workspace/query/src/github.com/couchbase/query/data/sampledb/default/tutorial/dave.json'"
        query = "select curl("+protocol+")"
        curl = self.shell.execute_commands_inside(self.cbqpath,query,'', '', '', '', '')
        actual_curl = self.convert_to_json(curl)
        self.assertTrue(actual_curl['errors'][0]['msg'] == 'Errorevaluatingprojection.-cause:curl:Unsupportedprotocol')

    '''Tests what happens when n1ql curl receives invalid urls
        -urls that don't exist
        -urls that don't return json'''
    def test_invalid_url(self):
        # Test url that does not exist
        url = "'http://asdsadasdsadsxfwefwefsdfqffsf.com/'"
        query="select curl("+ url +")"
        curl = self.shell.execute_commands_inside(self.cbqpath,query,'', '', '', '', '')
        actual_curl = self.convert_to_json(curl)
        self.assertTrue(actual_curl['errors'][0]['msg'] ==
                        "Errorevaluatingprojection.-cause:curl:Couldn'tresolvehostname")

        # Test a valid url that does not return json
        url = "'google.com'"
        query="select curl("+ url +")"
        curl = self.shell.execute_commands_inside(self.cbqpath,query,'', '', '', '', '')
        actual_curl = self.convert_to_json(curl)
        self.assertTrue(actual_curl['errors'][0]['msg'] ==
                        "Errorevaluatingprojection.-cause:InvalidJSONendpointgoogle.com")

    '''Test if a protected bucket can be accessed without giving its password'''
    def test_protected_bucket_noauth(self):
        error_msg = "Userdoesnothavecredentialstoaccessprivilegecluster.bucket[bucket0]." \
                    "n1ql.select!execute.AddroleQuerySelect[bucket0]toallowthequerytorun."
        # The query that curl will send to couchbase
        n1ql_query = 'select * from bucket0 limit 5'
        # This is the query that the cbq-engine will execute
        query = "select curl("+ self.query_service_url +", {'data' : 'statement=%s'})" % n1ql_query
        curl = self.shell.execute_commands_inside(self.cbqpath,query,'', '', '', '', '')
        json_curl = self.convert_to_json(curl)
        self.assertTrue(json_curl['results'][0]['$1']['errors'][0]['msg'] == error_msg)

        query = "select curl("+ self.query_service_url +", {'data' : 'statement=%s','user':'%s:'})" \
                                                        % (n1ql_query,self.username)
        curl = self.shell.execute_commands_inside(self.cbqpath,query,'', '', '', '', '')
        json_curl = self.convert_to_json(curl)
        self.assertTrue(json_curl['results'][0]['$1']['errors'][0]['msg'] == error_msg)

        query = "select curl("+ self.query_service_url +", {'data' : 'statement=%s','user':':%s'})" \
                                                        % (n1ql_query,self.password)
        curl = self.shell.execute_commands_inside(self.cbqpath,query,'', '', '', '', '')
        json_curl = self.convert_to_json(curl)
        self.assertTrue(json_curl['results'][0]['$1']['errors'][0]['msg'] == error_msg)

    '''Test an unsupported method in n1ql curl
        -DELETE.'''
    def test_unsupported_method(self):
        url = "'http://google.com/'"
        query="select curl("+ url +",{'request':'DELETE'})"
        curl = self.shell.execute_commands_inside(self.cbqpath,query,'', '', '', '', '')
        actual_curl = self.convert_to_json(curl)
        self.assertTrue(actual_curl['errors'][0]['msg'] ==
                        "Errorevaluatingprojection.-cause:CURLonlysupportsGETandPOSTrequests.")

    '''Tests what happens when you don't give an api key to a url that requires an api key
        - do not provide an api key
        - provide an incorrect api key'''
    def test_external_json_invalid_api_key(self):
        # Don't provide an apikey to a url that requires one
        url = "'https://api.themoviedb.org/3/movie/550'"
        query="select curl("+ url +")"
        curl = self.shell.execute_commands_inside(self.cbqpath,query,'', '', '', '', '')
        actual_curl = self.convert_to_json(curl)
        self.assertTrue(actual_curl['results'][0]['$1']['status_message'] == "InvalidAPIkey:Youmustbegrantedavalidkey.")

        # Test the google maps json endpoint with an invalid api key and make sure it errors
        url = "'https://maps.googleapis.com/maps/api/geocode/json'"
        options= "{'get':True,'data': 'address=santa+cruz&components=country:ES&key=AIzaSyCT6niGCMsgegJkQSYSqpoLZ4_'}"
        query="select curl("+ url +", %s" % options + ")"
        curl = self.shell.execute_commands_inside(self.cbqpath,query,'', '', '', '', '')
        actual_curl = self.convert_to_json(curl)
        self.assertTrue(actual_curl['results'][0]['$1']['error_message'] == "TheprovidedAPIkeyisinvalid.")

    '''Tests what happens when you try to access a site with different types of invalid certs'''
    def test_invalid_certs(self):
        #Error message is different for linux vs windows
        if self.info.type.lower() == 'windows':
            error_msg = "Errorevaluatingprojection.-cause:curl:SSLconnecterror"
            wrong_host_msg = str(error_msg)
        else:
            error_msg = "Errorevaluatingprojection.-cause:curl:Peercertificatecannotbeauthenticated" \
                        "withgivenCAcertificates"
            wrong_host_msg = "Errorevaluatingprojection.-cause:curl:SSLpeercertificateorSSHremotekey" \
                             "wasnotOK"
        url = "'https://expired.badssl.com/'"
        query="select curl("+ url +")"
        curl = self.shell.execute_commands_inside(self.cbqpath,query,'', '', '', '', '')
        actual_curl = self.convert_to_json(curl)
        self.assertTrue(actual_curl['errors'][0]['msg'] == error_msg)

        url = "'https://self-signed.badssl.com/'"
        query="select curl("+ url +")"
        curl = self.shell.execute_commands_inside(self.cbqpath,query,'', '', '', '', '')
        actual_curl = self.convert_to_json(curl)
        self.assertTrue(actual_curl['errors'][0]['msg'] == error_msg)

        url = "'https://wrong.host.badssl.com/'"
        query="select curl("+ url +")"
        curl = self.shell.execute_commands_inside(self.cbqpath,query,'', '', '', '', '')
        actual_curl = self.convert_to_json(curl)
        self.assertTrue(actual_curl['errors'][0]['msg'] == wrong_host_msg)

        url = "'https://superfish.badssl.com/'"
        query="select curl("+ url +")"
        curl = self.shell.execute_commands_inside(self.cbqpath,query,'', '', '', '', '')
        actual_curl = self.convert_to_json(curl)
        self.assertTrue(actual_curl['errors'][0]['msg'] == error_msg)

    '''Secret endpoints should not be accsible without the localtoken'''
    def test_secret_endpoints(self):
        error_msg = "Errorevaluatingprojection.-cause:curl:Couldn'tconnecttoserver"
        url = "'http://127.0.0.1:9000/diag/password'"
        query = "select curl(" + url + ")"
        curl = self.shell.execute_commands_inside(self.cbqpath, query, '', '', '', '', '')
        actual_curl = self.convert_to_json(curl)
        self.assertTrue(actual_curl['errors'][0]['msg'] == error_msg)

        url = "'http://127.0.0.1:9000/controller/resetAdminPassword'"
        query = "select curl(" + url + ",{'data':'password=asdasdadss','request':'POST'})"
        curl = self.shell.execute_commands_inside(self.cbqpath, query, '', '', '', '', '')
        actual_curl = self.convert_to_json(curl)
        self.assertTrue(actual_curl['errors'][0]['msg'] == error_msg)

    '''MB-22110: Tokenizer should throw a syntax error if invalid tokens are passed in'''
    def test_invalid_paramerterized_query(self):
        error_msg = "syntaxerror-at#"
        query = "select curl(" + self.query_service_url +", "
        options = "{'data':'statement=select ##3 from `beer-sample` limit 1'})"
        curl = self.shell.execute_commands_inside(self.cbqpath, query + options, '', '', '', '', '')
        json_curl = self.convert_to_json(curl)
        self.assertTrue(json_curl['results'][0]['$1']['errors'][0]['msg'] == error_msg)

##############################################################################################
#
#   RBAC Tests
#
##############################################################################################
    '''Test if a user without curl privileges can use curl and test if a user with curl privileges
       can use curl'''
    def test_curl_access(self):
        error_msg = "Userdoesnothavecredentialstoaccessprivilegecluster.n1ql.curl!execute.Addrole" \
                    "QueryExternalAccesstoallowthequerytorun."
        cbqpath = '%scbq' % self.path + " -u 'no_curl' -p 'password'"
        # The query that curl will send to couchbase
        n1ql_query = 'select * from default limit 5'
        # This is the query that the cbq-engine will execute
        query = "select curl("+ self.query_service_url +\
                ", {'data' : 'statement=%s','user':'no_curl:password'})" % (n1ql_query)
        curl = self.shell.execute_commands_inside(cbqpath,query,'', '', '', '', '')
        json_curl = self.convert_to_json(curl)
        self.assertTrue(json_curl['errors'][0]['msg'] == error_msg)

        cbqpath = '%scbq' % self.path + " -u 'curl' -p 'password'"
        query = "select curl("+ self.query_service_url +\
                ", {'data' : 'statement=%s','user':'curl:password'})" % (n1ql_query)
        curl = self.shell.execute_commands_inside(cbqpath,query,'', '', '', '', '')
        json_curl = self.convert_to_json(curl)
        # Compare the curl statement to the expected result of the n1ql query done normally
        expected_result = self.run_cbq_query(n1ql_query)
        self.assertTrue(json_curl['results'][0]['$1']['results'] == expected_result['results'])

    '''Test if curl can be used to grant roles without the correct permissions'''
    def test_curl_grant_role(self):
        error_msg = "Errorevaluatingprojection.-cause:InvalidJSONendpointhttp://%s:%s/" \
                    "settings/rbac/users/local/intuser" % (self.master.ip,self.master.port)
        cbqpath = '%scbq' % self.path + " -u 'curl' -p 'password'"
        query = "select curl('http://%s:%s/settings/rbac/users/local/intuser' " \
                % (self.master.ip,self.master.port) +\
                ", {'data' : 'name=TestInternalUser&roles=data_reader[default]&password=pwintuser'" \
                ",'user':'curl:password','request':'POST'})"
        curl = self.shell.execute_commands_inside(cbqpath,query,'', '', '', '', '')
        json_curl = self.convert_to_json(curl)
        self.assertTrue(json_curl['errors'][0]['msg'] == error_msg)

        error_msg = "Errorevaluatingprojection.-cause:CURLonlysupportsGETandPOSTrequests."
        query = "select curl('http://%s:%s/settings/rbac/users/local/intuser' " \
                % (self.master.ip,self.master.port) +\
                ", {'data' : 'name=Test Internal User&roles=data_reader[default]&password=pwintuser'" \
                ",'user':'curl:password','request':'PUT'})"
        curl = self.shell.execute_commands_inside(cbqpath,query,'', '', '', '', '')
        json_curl = self.convert_to_json(curl)
        self.assertTrue(json_curl['errors'][0]['msg'] == error_msg)

    '''Test if you can insert curl with a role that does not have curl, with a role that has curl but
       no insert privileges, and a combination of the two roles.'''
    def test_insert_no_role(self):
        error_msg = "Userdoesnothavecredentialstoaccessprivilegecluster.n1ql.curl!execute." \
                    "AddroleQueryExternalAccesstoallowthequerytorun."
        cbqpath = '%scbq' % self.path + " -u 'no_curl' -p 'password'"
        n1ql_query = 'select * from \`beer-sample\` limit 1'
        insert_query ="insert into default (key UUID(), value curl_result.results[0].\`beer-sample\`) "
        query = "select curl("+ self.query_service_url +", "
        options = "{'data' : 'statement=%s','user':'no_curl:password'}) curl_result " % (n1ql_query)
        returning ="returning meta().id, * "
        curl = self.shell.execute_commands_inside(cbqpath,insert_query+query+options+returning,'', '', '','', '')
        json_curl = self.convert_to_json(curl)
        self.assertTrue(json_curl['errors'][0]['msg'] == error_msg)

        error_msg= "Userdoesnothavecredentialstoaccessprivilegecluster.bucket[default].n1ql.insert!" \
                   "execute.AddroleQueryInsert[default]toallowthequerytorun."
        cbqpath = '%scbq' % self.path + " -u 'curl_no_insert' -p 'password'"
        options = "{'data' : 'statement=%s','user':'curl_no_insert:password'}) curl_result " \
                  % (n1ql_query)
        returning ="returning meta().id, * "
        curl = self.shell.execute_commands_inside(cbqpath,insert_query+query+options+returning,'', '', '','', '')
        json_curl = self.convert_to_json(curl)
        self.assertTrue(json_curl['errors'][0]['msg'] == error_msg)

        error_msg = "Userdoesnothavecredentialstoaccessprivilegecluster.n1ql.curl!execute.Addrole" \
                    "QueryExternalAccesstoallowthequerytorun."
        cbqpath = '%scbq' % self.path + " -u 'no_curl' -p 'password'"
        n1ql_query = 'select * from \`beer-sample\` limit 1'
        insert_query ="insert into default (key UUID(), value curl_result.results[0].\`beer-sample\`) "
        query = "select curl("+ self.query_service_url +", "
        options = "{'data' : 'statement=%s','user':'curl_no_insert:password'}) curl_result " \
                  % (n1ql_query)
        returning ="returning meta().id, * "
        curl = self.shell.execute_commands_inside(cbqpath,insert_query+query+options+returning,'', '', '','', '')
        json_curl = self.convert_to_json(curl)
        self.assertTrue(json_curl['errors'][0]['msg'] == error_msg)

    '''Test if curl privileges can be used to circumvent other privileges, (insert,update,delete)'''
    def test_circumvent_roles(self):
        error_msg = "Userdoesnothavecredentialstoaccessprivilegecluster.bucket[default].n1ql.insert!" \
                    "execute.AddroleQueryInsert[default]toallowthequerytorun."
        cbqpath = '%scbq' % self.path + " -u 'curl_no_insert' -p 'password'"
        curl_query = "select curl("+ self.query_service_url+ ", "
        options = "{'data':'statement=insert into default (key UUID(), value result) select result " \
                  "from curl(\\\"https://jira.atlassian.com/rest/api/latest/issue/JRA-9\\\") result " \
                  "returning meta().id, * '})"
        curl = self.shell.execute_commands_inside(cbqpath,curl_query+options,'', '', '','', '')
        json_curl = self.convert_to_json(curl)
        self.assertTrue(json_curl['results'][0]['$1']['errors'][0]['msg'] == error_msg)

        error_msg = "Userdoesnothavecredentialstoaccessprivilegecluster.bucket[default].n1ql.update!" \
                    "execute.AddroleQueryUpdate[default]toallowthequerytorun."
        query = 'select meta().id from default limit 1'
        result = self.run_cbq_query(query)
        docid = result['results'][0]['id']
        options = "{'data':'statement=update default use keys \\\"" + docid +"\\\" set name = (select result " \
                  "from curl(\\\"https://jira.atlassian.com/rest/api/latest/issue/JRA-9\\\") result) " \
                  "returning meta().id, * '})"
        curl = self.shell.execute_commands_inside(cbqpath,curl_query+options,'', '', '','', '')
        json_curl = self.convert_to_json(curl)
        self.assertTrue(json_curl['results'][0]['$1']['errors'][0]['msg'] == error_msg)

        error_msg = "Userdoesnothavecredentialstoaccessprivilegecluster.bucket[default].n1ql.delete!" \
                    "execute.AddroleQueryDelete[default]toallowthequerytorun."
        options = "{'data':'statement=delete from default use keys \\\"" + docid +"\\\"" \
                  "returning meta().id, * '})"
        curl = self.shell.execute_commands_inside(cbqpath,curl_query+options,'', '', '','', '')
        json_curl = self.convert_to_json(curl)
        self.assertTrue(json_curl['results'][0]['$1']['errors'][0]['msg'] == error_msg)

##############################################################################################
#
#   Helper Functions
#
##############################################################################################

    '''Convert output of remote_util.execute_commands_inside to json
        For some reason you cannot treat the output of execute_commands_inside as normal unicode, this is a workaround
        to convert the output to json'''
    def convert_to_json(self,output_curl):
        # There are 48 unnecessary characters in the output that must be removed(centos)
        # There are 64 unnescessary characters in the output that must be removed(windows)
        if self.info.type.lower() == 'windows':
            new_curl = json.dumps(output_curl[64:])
        else:
            new_curl = json.dumps(output_curl[47:])
        string_curl = json.loads(new_curl)
        json_curl = json.loads(string_curl)
        return json_curl

    '''Convert output of remote_util.execute_command to json
       (stripping all white space to match execute_command_inside output)'''
    def convert_list_to_json(self,output_of_curl):
        new_list = [string.replace(" ", "") for string in output_of_curl]
        concat_string = ''.join(new_list)
        json_output=json.loads(concat_string)
        return json_output

    '''Convert output of remote_util.execute_command to json to match the output of run_cbq_query'''
    def convert_list_to_json_with_spacing(self,output_of_curl):
        new_list = [string.strip() for string in output_of_curl]
        concat_string = ''.join(new_list)
        json_output=json.loads(concat_string)
        return json_output