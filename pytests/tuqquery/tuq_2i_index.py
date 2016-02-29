import math
import re
import uuid
import time

from tuq import QueryTests
from tuq import ExplainPlanHelper
from remote.remote_util import RemoteMachineShellConnection
from membase.api.rest_client import RestConnection
from membase.api.exception import CBQError

class QueriesIndexTests(QueryTests):

    FIELDS_TO_INDEX = [('name', 'job_title'), ('name', 'join_yr'), ('VMs', 'name')]
    COMPLEX_FIELDS_TO_INDEX = ['VMs', 'tasks_points', 'skills']
    def setUp(self):
        super(QueriesIndexTests, self).setUp()
        self.num_indexes = self.input.param('num_indexes', 1)
        if self.num_indexes > len(self.FIELDS_TO_INDEX):
            self.input.test_params["stop-on-failure"] = True
            self.log.error("MAX NUMBER OF INDEXES IS 3. ALL TESTS WILL BE SKIPPED")
            self.fail('MAX NUMBER OF INDEXES IS 3. ALL TESTS WILL BE SKIPPED')

    def suite_setUp(self):
        super(QueriesIndexTests, self).suite_setUp()

    def tearDown(self):
        super(QueriesIndexTests, self).tearDown()

    def suite_tearDown(self):
        super(QueriesIndexTests, self).suite_tearDown()

    def test_simple_array_index(self):
        for bucket in self.buckets:
            created_indexes = []
            try:
                idx = "idxjoin_yr"
                self.query = "CREATE INDEX %s ON %s( DISTINCT ARRAY v FOR v in %s END) USING %s" % (
                    idx, bucket.name, "join_yr", self.index_type)
                # if self.gsi_type:
                #     self.query += " WITH {'index_type': 'memdb'}"
                actual_result = self.run_cbq_query()
                self._wait_for_index_online(bucket, idx)
                self._verify_results(actual_result['results'], [])
                created_indexes.append(idx)
                self.assertTrue(self._is_index_in_list(bucket, idx), "Index is not in list")

                idx2 = "idxVM"
                self.query = "CREATE INDEX %s ON %s( DISTINCT ARRAY x.RAM FOR x in %s END) USING %s" % (
                    idx2, bucket.name, "VMs", self.index_type)
                # if self.gsi_type:
                #     self.query += " WITH {'index_type': 'memdb'}"
                actual_result = self.run_cbq_query()
                self._wait_for_index_online(bucket, idx2)
                self._verify_results(actual_result['results'], [])
                created_indexes.append(idx2)

                self.query = "Explain select name from %s where any v in %s.join_yr satisfies v = 2016 END " % (
                bucket.name, bucket.name) + \
                             "AND (ANY x IN %s.VMs SATISFIES x.RAM between 1 and 5 END) " % (bucket.name) + \
                             "AND  NOT (department = 'Manager') ORDER BY name limit 10"
                actual_result = self.run_cbq_query()

                self.assertTrue(
                    actual_result['results'][0]['~children'][0]['~children'][0]['#operator'] == 'IntersectScan',
                    "Intersect Scan is not being used in and query for 2 array indexes")

                result1 = actual_result['results'][0]['~children'][0]['~children'][0]['scans'][0]['scans'][0]['index']
                result2 = actual_result['results'][0]['~children'][0]['~children'][0]['scans'][1]['scans'][0]['index']
                self.assertTrue(result1 == idx2 or result1 == idx)
                self.assertTrue(result2 == idx or result2 == idx2)
                self.query = "select name from %s where any v in %s.join_yr satisfies v = 2016 END " % (
                bucket.name, bucket.name) + \
                             "AND (ANY x IN %s.VMs SATISFIES x.RAM between 1 and 5 END) " % (bucket.name) + \
                             "AND  NOT (department = 'Manager') ORDER BY name limit 10"
                actual_result = self.run_cbq_query()
                self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, idx, self.index_type)
                drop_result = self.run_cbq_query()
                self._verify_results(drop_result['results'], [])
                self.assertFalse(self._is_index_in_list(bucket, idx), "Index is in list")
                created_indexes.remove(idx)
                self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, idx2, self.index_type)
                drop_result = self.run_cbq_query()
                self._verify_results(drop_result['results'], [])
                self.assertFalse(self._is_index_in_list(bucket, idx2), "Index is in list")
                created_indexes.remove(idx2)

                idx3 = "idxjoin_yr2"
                self.query = "CREATE INDEX %s ON %s( DISTINCT ARRAY v FOR v within %s END) USING %s" % (
                    idx3, bucket.name, "join_yr", self.index_type)
                # if self.gsi_type:
                #     self.query += " WITH {'index_type': 'memdb'}"
                create_result = self.run_cbq_query()
                self._wait_for_index_online(bucket, idx3)
                self._verify_results(create_result['results'], [])
                created_indexes.append(idx3)
                self.assertTrue(self._is_index_in_list(bucket, idx3), "Index is not in list")
                idx4 = "idxVM2"
                self.query = "CREATE INDEX %s ON %s( DISTINCT ARRAY x.RAM FOR x within %s END) USING %s" % (
                    idx4, bucket.name, "VMs", self.index_type)
                # if self.gsi_type:
                #     self.query += " WITH {'index_type': 'memdb'}"
                create_result = self.run_cbq_query()
                self._wait_for_index_online(bucket, idx4)
                self._verify_results(create_result['results'], [])
                created_indexes.append(idx4)

                self.assertTrue(self._is_index_in_list(bucket, idx4), "Index is not in list")

                self.query = "Explain select name from %s where any v in %s.join_yr satisfies v = 2016 END " % (
                bucket.name, bucket.name) + \
                             "AND (ANY x IN %s.VMs SATISFIES x.RAM between 1 and 5 END) " % (bucket.name) + \
                             "AND  NOT (department = 'Manager') ORDER BY name limit 10"
                actual_result_within = self.run_cbq_query()
                self.assertTrue(
                    actual_result_within['results'][0]['~children'][0]['~children'][0]['#operator'] == 'IntersectScan',
                    "Intersect Scan is not being used in and query for 2 array indexes")

                result1 = actual_result_within['results'][0]['~children'][0]['~children'][0]['scans'][0]['scans'][0]['index']
                result2 = actual_result_within['results'][0]['~children'][0]['~children'][0]['scans'][1]['scans'][0]['index']
                self.assertTrue(result1 == idx3 or result1 == idx4)
                self.assertTrue(result2 == idx4 or result2 == idx3)


                self.query = "Explain select name from %s where any v within %s.join_yr satisfies v = 2016 END " % (
                bucket.name, bucket.name) + \
                             "AND (ANY x within %s.VMs SATISFIES x.RAM between 1 and 5 END) " % (bucket.name) + \
                             "AND  NOT (department = 'Manager') ORDER BY name limit 10"
                actual_result_within = self.run_cbq_query()

                self.assertTrue(
                    actual_result_within['results'][0]['~children'][0]['~children'][0]['#operator'] == 'IntersectScan',
                    "Intersect Scan is not being used in and query for 2 array indexes")

                result3 = actual_result_within['results'][0]['~children'][0]['~children'][0]['scans'][0]['scans'][0]['index']
                result4 = actual_result_within['results'][0]['~children'][0]['~children'][0]['scans'][1]['scans'][0]['index']

                self.assertTrue(result3 == idx4 or result3 == idx3)
                self.assertTrue(result4 == idx3 or result4 == idx4)
                self.query = "select name from %s where any v in %s.join_yr satisfies v = 2016 END " % (
                bucket.name, bucket.name) + \
                             "AND (ANY x within %s.VMs SATISFIES x.RAM between 1 and 5  END ) " % (bucket.name) + \
                             "AND  NOT (department = 'Manager') order by name limit 10"
                actual_result_within = self.run_cbq_query()
                expected_result = [{"name": doc['name']}
                                   for doc in self.full_list
                                   if len([yr for yr in doc["join_yr"]
                                           if yr == 2016]) > 0 and \
                                   len([vm for vm in doc["VMs"]
                                        if 0 < vm['RAM'] < 6]) > 0 and \
                                   doc["department"] != 'Manager']
                expected_result = sorted(expected_result, key=lambda doc: (doc['name']))[0:10]
                self.assertTrue(actual_result['results'] == expected_result)
                #self.assertTrue(actual_result_within['results'] == expected_result)
            finally:
                for idx in created_indexes:
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, idx, self.index_type)
                    actual_result = self.run_cbq_query()
                    self._verify_results(actual_result['results'], [])
                    self.assertFalse(self._is_index_in_list(bucket, idx), "Index is in list")

    def test_simple_nested_index(self):
        for bucket in self.buckets:
            created_indexes = []
            try:
                idx = "nested_idx"
                self.query = "CREATE INDEX %s ON %s( DISTINCT ARRAY ( DISTINCT array j for j in i end) FOR i in %s END) USING %s" % (
                    idx, bucket.name, "tasks", self.index_type)
                # if self.gsi_type:
                #     self.query += " WITH {'index_type': 'memdb'}"
                actual_result = self.run_cbq_query()
                self._wait_for_index_online(bucket, idx)
                self._verify_results(actual_result['results'], [])
                created_indexes.append(idx)
                self.assertTrue(self._is_index_in_list(bucket, idx), "Index is not in list")

                idx2 = "idxtasks"
                self.query = "CREATE INDEX %s ON %s( DISTINCT ARRAY x FOR x in %s END) USING %s" % (
                    idx2, bucket.name, "tasks", self.index_type)
                # if self.gsi_type:
                #     self.query += " WITH {'index_type': 'memdb'}"
                actual_result = self.run_cbq_query()
                self._wait_for_index_online(bucket, idx2)
                self._verify_results(actual_result['results'], [])
                created_indexes.append(idx2)

                self.assertTrue(self._is_index_in_list(bucket, idx2), "Index is not in list")

                self.query = "Explain select name from %s WHERE ANY i IN %s.tasks SATISFIES  (ANY j IN i SATISFIES j='Search' end) END " % (
                bucket.name,bucket.name) + \
                             "AND (ANY x IN %s.tasks SATISFIES x = 'Sales' END) " % (bucket.name) + \
                             "AND  NOT (department = 'Manager') order BY name limit 10"
                actual_result = self.run_cbq_query()
                self.assertTrue(
                    actual_result['results'][0]['~children'][0]['~children'][0]['#operator'] == 'IntersectScan',
                    "Intersect Scan is not being used in and query for 2 array indexes")
                result1 = actual_result['results'][0]['~children'][0]['~children'][0]['scans'][0]['scans'][0]['index']
                result2 = actual_result['results'][0]['~children'][0]['~children'][0]['scans'][1]['scans'][0]['index']
                self.assertTrue(result1 == idx2 or result1 == idx)
                self.assertTrue(result2 == idx or result2 == idx2)
                actual_result = self.run_cbq_query()
                self.query = "select name from %s WHERE ANY i IN %s.tasks SATISFIES  (ANY j IN i SATISFIES j='Search' end) END " % (
                bucket.name,bucket.name) + \
                             "AND (ANY x IN %s.tasks SATISFIES x = 'Sales' END) " % (bucket.name) + \
                             "AND  NOT (department = 'Manager') order BY name limit 10"
                actual_result = self.run_cbq_query()
                # expected_result = [{"name": doc['name']}
                #                    for doc in self.full_list
                #                    if len([yr for yr in doc["join_yr"]
                #                            if yr == 2016]) > 0 and \
                #                    len([vm for vm in doc["VMs"]
                #                         if 0 < vm['RAM'] < 6]) > 0 and \
                #                    doc["department"] != 'Manager']
                # expected_result = sorted(expected_result, key=lambda doc: (doc['name']))[0:10]
                # print expected_result
                # self.assertTrue(actual_result['results'] == expected_result)
            finally:
                for idx in created_indexes:
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, idx, self.index_type)
                    actual_result = self.run_cbq_query()
                    self._verify_results(actual_result['results'], [])
                    self.assertFalse(self._is_index_in_list(bucket, idx), "Index is in list")

    def test_nested_attr_index(self):
        for bucket in self.buckets:
            created_indexes = []
            try:
                idx = "nested_idx_attr"
                self.query = "CREATE INDEX %s ON %s( DISTINCT ARRAY ( DISTINCT array j.region1 for j in i.Marketing end) FOR i in %s END) USING %s" % (
                    idx, bucket.name, "tasks", self.index_type)
                # if self.gsi_type:
                #     self.query += " WITH {'index_type': 'memdb'}"
                actual_result = self.run_cbq_query()
                self._wait_for_index_online(bucket, idx)
                self._verify_results(actual_result['results'], [])
                created_indexes.append(idx)
                self.assertTrue(self._is_index_in_list(bucket, idx), "Index is not in list")


                self.query = "Explain select name from %s WHERE ANY i IN %s.tasks SATISFIES  (ANY j IN i.Marketing SATISFIES j.region1='South' end) END " % (
                bucket.name,bucket.name) + \
                             "order BY name limit 10"
                actual_result = self.run_cbq_query()
                self.assertTrue(
                    actual_result['results'][0]['~children'][0]['~children'][0]['#operator'] == 'UnionScan',
                    "Union Scan is not being used")
                result1 = actual_result['results'][0]['~children'][0]['~children'][0]['scans'][0]['index']
                self.assertTrue(result1 == idx)
                actual_result = self.run_cbq_query()
                self.query = "select name from %s WHERE ANY i IN %s.tasks SATISFIES  (ANY j IN i.Marketing SATISFIES j.region1='South' end) END " % (
                bucket.name,bucket.name) + \
                             "order BY name limit 10"
                actual_result = self.run_cbq_query()
            finally:
                for idx in created_indexes:
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, idx, self.index_type)
                    actual_result = self.run_cbq_query()
                    self._verify_results(actual_result['results'], [])
                    self.assertFalse(self._is_index_in_list(bucket, idx), "Index is in list")

    def test_nested_attr_index_within(self):
        for bucket in self.buckets:
            created_indexes = []
            try:
                idx = "nested_idx_attr"
                self.query = "CREATE INDEX %s ON %s( DISTINCT ARRAY i FOR i within %s END) USING %s" % (
                    idx, bucket.name, "hobbies", self.index_type)
                # if self.gsi_type:
                #     self.query += " WITH {'index_type': 'memdb'}"
                actual_result = self.run_cbq_query()
                self._wait_for_index_online(bucket, idx)
                self._verify_results(actual_result['results'], [])
                created_indexes.append(idx)
                self.assertTrue(self._is_index_in_list(bucket, idx), "Index is not in list")

                self.query = "Explain select name from %s WHERE ANY i within %s.hobbies SATISFIES i = 'bhangra' END " % (
                bucket.name,bucket.name) + \
                             "order BY name limit 10"
                actual_result = self.run_cbq_query()
                self.assertTrue(
                    actual_result['results'][0]['~children'][0]['~children'][0]['#operator'] == 'UnionScan',
                    "UnionScan is not being used")
                result1 = actual_result['results'][0]['~children'][0]['~children'][0]['scans'][0]['index']
                self.assertTrue(result1 == idx)

                self.query = "select name from %s WHERE ANY i within %s.hobbies SATISFIES i = 'bhangra' END " % (
                bucket.name,bucket.name) + \
                             "order BY name limit 10"
                actual_result = self.run_cbq_query()
                actual_result = sorted(actual_result['results'])
                self.query = "select name from %s USE INDEX(`#primary`) WHERE ANY i within %s.hobbies SATISFIES i = 'bhangra' END " % (
                bucket.name,bucket.name) + \
                             "order BY name limit 10"
                expected_result = self.run_cbq_query()
                expected_result = sorted(expected_result['results'])
                #self.assertTrue(actual_result,expected_result)
            finally:
                for idx in created_indexes:
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, idx, self.index_type)
                    actual_result = self.run_cbq_query()
                    self._verify_results(actual_result['results'], [])
                    self.assertFalse(self._is_index_in_list(bucket, idx), "Index is in list")

    def test_nested_attr_array_index_in(self):
        for bucket in self.buckets:
            created_indexes = []
            try:
                idx = "nested_idx_attr"
                self.query = "CREATE INDEX %s ON %s( DISTINCT ARRAY ( DISTINCT array j for j in i.dance end) FOR i in %s END) USING %s" % (
                    idx, bucket.name, "hobbies.hobby", self.index_type)
                # if self.gsi_type:
                #     self.query += " WITH {'index_type': 'memdb'}"
                actual_result = self.run_cbq_query()
                self._wait_for_index_online(bucket, idx)
                self._verify_results(actual_result['results'], [])
                created_indexes.append(idx)
                self.assertTrue(self._is_index_in_list(bucket, idx), "Index is not in list")

                self.query = "explain select name from %s WHERE ANY i IN %s.hobbies.hobby SATISFIES  (ANY j IN i.dance SATISFIES j='contemporary' end) END and department='Support'" % (
                bucket.name,bucket.name) + \
                             "order BY name limit 10"
                actual_result = self.run_cbq_query()
                self.assertTrue(
                    actual_result['results'][0]['~children'][0]['~children'][0]['#operator'] == 'UnionScan',
                    "UnionScan is not being used")
                result1 = actual_result['results'][0]['~children'][0]['~children'][0]['scans'][0]['index']
                self.assertTrue(result1 == idx)
                actual_result = self.run_cbq_query()
                self.query = "select name from %s WHERE ANY i IN %s.hobbies.hobby SATISFIES  (ANY j IN i.dance SATISFIES j='contemporary' end) END and department='Support'" % (
                bucket.name,bucket.name) + \
                             "order BY name limit 10"
                actual_result = self.run_cbq_query()
                actual_result = sorted(actual_result['results'])
                self.query = "select name from %s USE INDEX(`#primary`) WHERE ANY i IN %s.hobbies.hobby SATISFIES  (ANY j IN i.dance SATISFIES j='contemporary' end) END and department='Support'" % (
                bucket.name,bucket.name) + \
                             "order BY name limit 10"
                expected_result = self.run_cbq_query()
                expected_result = sorted(expected_result['results'])
                #self.assertTrue(actual_result,expected_result)
            finally:
                for idx in created_indexes:
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, idx, self.index_type)
                    actual_result = self.run_cbq_query()
                    self._verify_results(actual_result['results'], [])
                    self.assertFalse(self._is_index_in_list(bucket, idx), "Index is in list")

    def test_array_partial_index(self):
        for bucket in self.buckets:
            created_indexes = []
            try:
                idx = "idx"
                self.query = "CREATE INDEX %s ON %s(all distinct array i FOR i in %s END) WHERE (department = 'Support')  USING %s" % (
                  idx, bucket.name, "hobbies.hobby", self.index_type)
                # if self.gsi_type:
                #     self.query += " WITH {'index_type': 'memdb'}"
                actual_result = self.run_cbq_query()
                self._wait_for_index_online(bucket, idx)
                self._verify_results(actual_result['results'], [])
                created_indexes.append(idx)
                self.assertTrue(self._is_index_in_list(bucket, idx), "Index is not in list")
                self.query = "Explain select * from %s WHERE department = 'Support' and (ANY i IN %s.hobbies.hobby SATISFIES  i = 'art' END) " % (
                bucket.name,bucket.name) + \
                             "order BY name limit 10"
                actual_result = self.run_cbq_query()
                self.assertTrue(
                    actual_result['results'][0]['~children'][0]['~children'][0]['#operator'] == 'UnionScan',
                    "Union Scan is not being used")
                result1 = actual_result['results'][0]['~children'][0]['~children'][0]['scans'][0]['index']
                self.assertTrue(result1 == idx)
                self.query = "select name from %s WHERE department = 'Support' and ANY i IN %s.hobbies.hobby SATISFIES i = 'art' END " % (
                bucket.name,bucket.name) + \
                             "order BY name limit 10"
                actual_result = self.run_cbq_query()
                actual_result = sorted(actual_result['results'])
                self.query = "select name from %s WHERE department = 'Support' and ANY i IN %s.hobbies.hobby SATISFIES i = 'art' END " % (
                bucket.name,bucket.name) + \
                             "order BY name limit 10"
                expected_result = self.run_cbq_query()
                #self.assertTrue(actual_result,expected_result)
            finally:
                for idx in created_indexes:
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, idx, self.index_type)
                    actual_result = self.run_cbq_query()
                    self._verify_results(actual_result['results'], [])
                    self.assertFalse(self._is_index_in_list(bucket, idx), "Index is in list")

    def test_array_index_with_inner_joins(self):
        for bucket in self.buckets:
             created_indexes = []
             try:
                idx = "nested_inner_join"
                self.query = "CREATE INDEX %s ON %s( DISTINCT ARRAY ( DISTINCT array j.city for j in i end) FOR i in %s END) USING %s" % (
                    idx, bucket.name, "address", self.index_type)
                # if self.gsi_type:
                #     self.query += " WITH {'index_type': 'memdb'}"
                actual_result = self.run_cbq_query()
                self._wait_for_index_online(bucket, idx)
                self._verify_results(actual_result['results'], [])
                created_indexes.append(idx)
                self.assertTrue(self._is_index_in_list(bucket, idx), "Index is not in list")
                self.query = "explain SELECT new_project_full.department new_project " +\
                "FROM %s as employee  JOIN default as new_project_full " % (bucket.name) +\
                "ON KEYS employee._id WHERE ANY i IN employee.address SATISFIES  (ANY j IN i SATISFIES j.city='Delhi' end) END "
                actual_result = self.run_cbq_query()
                actual_result = (actual_result['results'])
                self.assertTrue(
                    actual_result[0]['~children'][0]['#operator'] == 'UnionScan',
                    "Union Scan is not being used")
                result1 = actual_result[0]['~children'][0]['scans'][0]['index']
                self.assertTrue(result1 == idx)
             finally:
                for idx in created_indexes:
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, idx, self.index_type)
                    actual_result = self.run_cbq_query()
                    self._verify_results(actual_result['results'], [])
                    self.assertFalse(self._is_index_in_list(bucket, idx), "Index is in list")

    def test_array_index_left_outer_join(self):
        for bucket in self.buckets:
             created_indexes = []
             try:
                idx = "outer_join"
                self.query = "CREATE INDEX %s ON %s( DISTINCT ARRAY ( DISTINCT array j.city for j in i end) FOR i in %s END) USING %s" % (
                    idx, bucket.name, "address", self.index_type)
                # if self.gsi_type:
                #     self.query += " WITH {'index_type': 'memdb'}"
                actual_result = self.run_cbq_query()
                self._wait_for_index_online(bucket, idx)
                self._verify_results(actual_result['results'], [])
                created_indexes.append(idx)
                self.assertTrue(self._is_index_in_list(bucket, idx), "Index is not in list")
                self.query = "explain SELECT new_project_full.department new_project " +\
                "FROM %s as employee left JOIN default as new_project_full " % (bucket.name) +\
                "ON KEYS employee._id WHERE ANY i IN employee.address SATISFIES  (ANY j IN i SATISFIES j.city='Delhi' end) END "
                actual_result = self.run_cbq_query()
                actual_result = (actual_result['results'])
                self.assertTrue(
                    actual_result[0]['~children'][0]['#operator'] == 'UnionScan',
                    "Union Scan is not being used")
                result1 = actual_result[0]['~children'][0]['scans'][0]['index']
                self.assertTrue(result1 == idx)
                self.query = "SELECT new_project_full.department new_project " +\
                "FROM %s as employee left JOIN default as new_project_full " % (bucket.name) +\
                "ON KEYS employee._id WHERE ANY i IN employee.address SATISFIES  (ANY j IN i SATISFIES j.city='Delhi' end) END "
                actual_result = self.run_cbq_query()
                actual_result = (actual_result['results'])
             finally:
                for idx in created_indexes:
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, idx, self.index_type)
                    actual_result = self.run_cbq_query()
                    self._verify_results(actual_result['results'], [])
                    self.assertFalse(self._is_index_in_list(bucket, idx), "Index is in list")

    def test_array_index_regexp(self):
        for bucket in self.buckets:
            created_indexes = []
            try:
                idx = "iregex"
                self.query = " CREATE INDEX %s ON %s( DISTINCT ARRAY REGEXP_LIKE(v.os,%s)  FOR v IN VMs END )  USING %s" % (
                  idx, bucket.name,"'ub%'", self.index_type)
                # if self.gsi_type:
                #     self.query += " WITH {'index_type': 'memdb'}"
                actual_result = self.run_cbq_query()
                self._wait_for_index_online(bucket, idx)
                self._verify_results(actual_result['results'], [])
                created_indexes.append(idx)
                self.assertTrue(self._is_index_in_list(bucket, idx), "Index is not in list")
                self.query = "explain select * from %s  WHERE ANY v IN VMs SATISFIES REGEXP_LIKE(v.os,%s) = 1 END  " % (
                bucket.name,"'ub%'") + \
                             "order BY name limit 10"
                actual_result = self.run_cbq_query()
                actual_result = actual_result['results']
                self.assertTrue(
                    actual_result[0]['~children'][0]['~children'][0]['#operator'] == 'UnionScan',
                    "Union Scan is not being used")
                result1 = actual_result[0]['~children'][0]['~children'][0]['scans'][0]['index']
                self.assertTrue(result1 == idx)
                self.query = "select name from %s use index(`#primary`)  WHERE ANY v IN VMs SATISFIES REGEXP_LIKE(v.os,%s) = 1  END  " % (
                bucket.name,"'ub%'") + \
                             "order BY name limit 10"

                expected_result = self.run_cbq_query()
                expected_result = expected_result['results']

                #self.assertTrue(sorted(expected_result,sorted(actual_result)))
            finally:
                for idx in created_indexes:
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, idx, self.index_type)
                    actual_result = self.run_cbq_query()
                    self._verify_results(actual_result['results'], [])
                    self.assertFalse(self._is_index_in_list(bucket, idx), "Index is in list")

    def test_array_index_greatest(self):
        for bucket in self.buckets:
            created_indexes = []
            try:
                idx = "igreatest"
                self.query = " CREATE INDEX %s ON %s(department, DISTINCT ARRAY GREATEST(v.RAM,100)  FOR v IN VMs END )  USING %s" % (
                    idx, bucket.name, self.index_type)
                # if self.gsi_type:
                #     self.query += " WITH {'index_type': 'memdb'}"
                actual_result = self.run_cbq_query()
                self._wait_for_index_online(bucket, idx)
                self._verify_results(actual_result['results'], [])
                created_indexes.append(idx)
                self.assertTrue(self._is_index_in_list(bucket, idx), "Index is not in list")
                self.query = "explain select name from %s WHERE department = 'Support' and ANY v IN VMs SATISFIES GREATEST(v.RAM,100) END " % (
                    bucket.name)
                actual_result = self.run_cbq_query()
                actual_result = actual_result['results']
                self.assertTrue(
                    actual_result[0]['~children'][0]['#operator'] == 'UnionScan',
                    "Union Scan is not being used")
                result1 = actual_result[0]['~children'][0]['scans'][0]['index']
                self.assertTrue(result1 == idx)
                self.query = "select name from %s WHERE department = 'Support' and ANY v IN VMs SATISFIES GREATEST(v.RAM,100) END " % (
                    bucket.name)
                actual_result = self.run_cbq_query()
                actual_result = actual_result['results']
                self.query = "select name from %s USE index(`#primary`) WHERE department = 'Support' and ANY v IN VMs SATISFIES GREATEST(v.RAM,100) END " % (
                    bucket.name)
                expected_result = self.run_cbq_query()
                expected_result = expected_result['results']
                #self.assertTrue(sorted(expected_result),sorted(actual_result))

            finally:
                for idx in created_indexes:
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, idx, self.index_type)
                    actual_result = self.run_cbq_query()
                    self._verify_results(actual_result['results'], [])
                    self.assertFalse(self._is_index_in_list(bucket, idx), "Index is in list")

    def test_array_index_nest_keys_where(self):
        for bucket in self.buckets:
            created_indexes = []
            try:
                idx = "nestidx"
                self.query = "CREATE INDEX %s ON %s( DISTINCT ARRAY j for j in department end) USING %s" % (
                    idx, bucket.name, self.index_type)
                if self.gsi_type:
                    self.query += " WITH {'index_type': 'memdb'}"
                actual_result = self.run_cbq_query()
                self._wait_for_index_online(bucket, idx)
                self._verify_results(actual_result['results'], [])
                created_indexes.append(idx)
                self.assertTrue(self._is_index_in_list(bucket, idx), "Index is not in list")
                self.query = "explain select emp.name, ARRAY item.department FOR item in items end departments " + \
                         "FROM %s emp NEST %s items " % (
                             bucket.name, bucket.name) + \
                         "ON KEYS emp._id  where  ANY j IN emp.department SATISFIES  j = 'Support' end;"
                actual_result = self.run_cbq_query()
                actual_result = actual_result['results']
                self.assertTrue(
                    actual_result[0]['~children'][0]['#operator'] == 'UnionScan',
                    "Union Scan is not being used")
                result1 = actual_result[0]['~children'][0]['scans'][0]['index']
                self.assertTrue(result1 == idx)
                self.query = "select emp.name, ARRAY item.department FOR item in items end departments " + \
                         "FROM %s emp NEST %s items " % (
                             bucket.name, bucket.name) + \
                         "ON KEYS emp._id  where  ANY j IN emp.department SATISFIES  j = 'Support' end;"
                actual_result = self.run_cbq_query()
                actual_result = sorted(actual_result['results'])
                self.query = "select emp.name, ARRAY item.department FOR item in items end departments " + \
                         "FROM %s emp USE INDEX(`#primary`) NEST %s items " % (
                             bucket.name, bucket.name) + \
                         "ON KEYS emp._id  where  ANY j IN emp.department SATISFIES  j = 'Support' end;"
                expected_result = self.run_cbq_query()
                expected_result = sorted(expected_result['results'])
                self.assertTrue(actual_result == expected_result)
            # actual_result = sorted(actual_result, key=lambda doc: (doc['name'], doc['projects']))
            # full_list = self._generate_full_nested_docs_list(join_type=self.type_join)
            # expected_result = [{"name" : doc['item']['name'],
            #                    "projects" : [nested_doc['project'] for nested_doc in doc['items_nested']]}
            #                   for doc in full_list if doc and 'items_nested' in doc and\
            #                   len([nested_doc for nested_doc in doc['items_nested']
            #                        if nested_doc['project'] == 'CB']) > 0]
            # expected_result = self.sort_nested_list(expected_result, key='projects')
            # expected_result = sorted(expected_result, key=lambda doc: (doc['name'], doc['projects']))
            # self._verify_results(actual_result, expected_result)

            finally:
                for idx in created_indexes:
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, idx, self.index_type)
                    actual_result = self.run_cbq_query()
                    self._verify_results(actual_result['results'], [])
                    self.assertFalse(self._is_index_in_list(bucket, idx), "Index is in list")

    def test_nest_keys_where_not_equal(self):
        for bucket in self.buckets:
            created_indexes = []
            try:
                idx = "nestidx"
                self.query = "CREATE INDEX %s ON %s( DISTINCT ARRAY j for j in department end) USING %s" % (
                    idx, bucket.name, self.index_type)
                if self.gsi_type:
                    self.query += " WITH {'index_type': 'memdb'}"
                actual_result = self.run_cbq_query()
                self._wait_for_index_online(bucket, idx)
                self._verify_results(actual_result['results'], [])
                created_indexes.append(idx)
                self.assertTrue(self._is_index_in_list(bucket, idx), "Index is not in list")
                self.query = "explain select emp.name, ARRAY item.department FOR item in items end departments " + \
                         "FROM %s emp NEST %s items " % (
                             bucket.name, bucket.name) + \
                         "ON KEYS emp._id  where  ANY j IN emp.department SATISFIES  j != 'Engineer' end;"
                actual_result = self.run_cbq_query()
                actual_result = actual_result['results']
                self.assertTrue(
                    actual_result[0]['~children'][0]['#operator'] == 'UnionScan',
                    "Union Scan is not being used")
                result1 = actual_result[0]['~children'][0]['scans'][0]['index']
                self.assertTrue(result1 == idx)
                self.query = "select emp.name, ARRAY item.department FOR item in items end departments " + \
                         "FROM %s emp NEST %s items " % (
                             bucket.name, bucket.name) + \
                         "ON KEYS emp._id  where  ANY j IN emp.department SATISFIES  j = 'Support' end;"
                actual_result = self.run_cbq_query()
                actual_result = actual_result['results']
                self.query = "select emp.name, ARRAY item.department FOR item in items end departments " + \
                         "FROM %s emp USE INDEX(`#primary`)   NEST %s items " % (
                             bucket.name, bucket.name) + \
                         "ON KEYS emp._id where  ANY j IN emp.department SATISFIES  j = 'Support' end;"
                expected_result = self.run_cbq_query()
                expected_result = sorted(expected_result['results'])
                self.assertTrue(sorted(actual_result) == expected_result)
            finally:
                for idx in created_indexes:
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, idx, self.index_type)
                    actual_result = self.run_cbq_query()
                    self._verify_results(actual_result['results'], [])
                    self.assertFalse(self._is_index_in_list(bucket, idx), "Index is in list")

    def test_nest_keys_where_between(self):
        for bucket in self.buckets:
            created_indexes = []
            try:
                idx = "nestidx"
                self.query = "CREATE INDEX %s ON %s( DISTINCT ARRAY j for j in join_yr end) USING %s" % (
                    idx, bucket.name, self.index_type)
                if self.gsi_type:
                    self.query += " WITH {'index_type': 'memdb'}"
                actual_result = self.run_cbq_query()
                self._wait_for_index_online(bucket, idx)
                self._verify_results(actual_result['results'], [])
                created_indexes.append(idx)
                self.assertTrue(self._is_index_in_list(bucket, idx), "Index is not in list")
                self.query = "explain select emp.name, ARRAY item.department FOR item in items end departments " + \
                         "FROM %s emp NEST %s items " % (
                             bucket.name, bucket.name) + \
                         "ON KEYS emp._id  where  ANY j IN emp.join_yr SATISFIES  j between 2010 and 2012 end;"
                actual_result = self.run_cbq_query()
                actual_result = actual_result['results']
                self.assertTrue(
                    actual_result[0]['~children'][0]['#operator'] == 'UnionScan',
                    "Union Scan is not being used")
                result1 = actual_result[0]['~children'][0]['scans'][0]['index']
                self.assertTrue(result1 == idx)
                self.query = "select emp.name, ARRAY item.department FOR item in items end departments " + \
                         "FROM %s emp NEST %s items " % (
                             bucket.name, bucket.name) + \
                         "ON KEYS emp._id  where  ANY j IN emp.department SATISFIES  j = 'Support' end;"
                actual_result = self.run_cbq_query()
                actual_result = sorted(actual_result['results'])
                self.query = "select emp.name, ARRAY item.department FOR item in items end departments " + \
                         "FROM %s emp USE INDEX(`#primary`)  NEST %s items " % (
                             bucket.name, bucket.name) + \
                         "ON KEYS emp._id  where  ANY j IN emp.department SATISFIES  j = 'Support' end;"
                expected_result = self.run_cbq_query()
                expected_result = sorted(expected_result['results'])
                self.assertTrue(actual_result == expected_result)
            finally:
                for idx in created_indexes:
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, idx, self.index_type)
                    actual_result = self.run_cbq_query()
                    self._verify_results(actual_result['results'], [])
                    self.assertFalse(self._is_index_in_list(bucket, idx), "Index is in list")

    def test_nest_keys_where_less_more_equal(self):
        for bucket in self.buckets:
            created_indexes = []
            try:
                idx = "nestidx"
                self.query = "CREATE INDEX %s ON %s( DISTINCT ARRAY j for j in join_yr end) USING %s" % (
                    idx, bucket.name, self.index_type)
                if self.gsi_type:
                    self.query += " WITH {'index_type': 'memdb'}"
                actual_result = self.run_cbq_query()
                self._wait_for_index_online(bucket, idx)
                self._verify_results(actual_result['results'], [])
                created_indexes.append(idx)
                self.assertTrue(self._is_index_in_list(bucket, idx), "Index is not in list")
                self.query = "explain select emp.name, ARRAY item.department FOR item in items end departments " + \
                         "FROM %s emp NEST %s items " % (
                             bucket.name, bucket.name) + \
                         "ON KEYS emp._id  where  ANY j IN emp.join_yr SATISFIES  j <= 2014 and j >= 2012 end;"
                actual_result = self.run_cbq_query()
                actual_result = actual_result['results']
                self.assertTrue(
                    actual_result[0]['~children'][0]['#operator'] == 'UnionScan',
                    "Union Scan is not being used")
                result1 = actual_result[0]['~children'][0]['scans'][0]['index']
                self.assertTrue(result1 == idx)
                self.query = "select emp.name, ARRAY item.department FOR item in items end departments " + \
                         "FROM %s emp NEST %s items " % (
                             bucket.name, bucket.name) + \
                         "ON KEYS emp._id  where  ANY j IN emp.department SATISFIES  j = 'Support' end;"
                actual_result = self.run_cbq_query()
                actual_result = sorted(actual_result['results'])
                self.query = "select emp.name, ARRAY item.department FOR item in items end departments " + \
                         "FROM %s emp USE INDEX(`#primary`)  NEST %s items " % (
                             bucket.name, bucket.name) + \
                         "ON KEYS emp._id  where  ANY j IN emp.department SATISFIES  j = 'Support' end;"
                expected_result = self.run_cbq_query()
                expected_result = sorted(expected_result['results'])
                self.assertTrue(actual_result == expected_result)
            finally:
                for idx in created_indexes:
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, idx, self.index_type)
                    actual_result = self.run_cbq_query()
                    self._verify_results(actual_result['results'], [])
                    self.assertFalse(self._is_index_in_list(bucket, idx), "Index is in list")



    def test_array_index_sum(self):
        for bucket in self.buckets:
            created_indexes = []
            for ind in xrange(self.num_indexes):
                    index_name = "indexwitharraysum%s" % ind
                    self.query = "CREATE INDEX %s ON %s(department, DISTINCT ARRAY round(v.memory + v.RAM) FOR v in VMs END ) where join_yr=2012 USING %s" % (index_name, bucket.name,self.index_type)
                    if self.gsi_type:
                        self.query += " WITH {'index_type': 'memdb'}"
                    self.run_cbq_query()
                    created_indexes.append(index_name)

        for bucket in self.buckets:
            try:
                for index_name in created_indexes:
                    self.query = "explain SELECT count(name),department" + \
                                 " FROM %s where join_yr=2012 AND ANY v IN VMs SATISFIES round(v.memory+v.RAM)<100 END AND department = 'Engineer'  GROUP BY department" % (bucket.name)
                    actual_result = self.run_cbq_query()
                    actual_result = (actual_result['results'])
                    self.assertTrue(
                    actual_result[0]['~children'][0]['#operator'] == 'UnionScan',
                    "Union Scan is not being used")
                    result1 = actual_result[0]['~children'][0]['scans'][0]['index']
                    self.assertTrue(result1 == index_name)

            finally:
                for index_name in set(created_indexes):
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, index_name,self.index_type)
                    self.run_cbq_query()

    def test_array_index_substring(self):
        for bucket in self.buckets:
            created_indexes = []
            try:
                idx = "idxsubstr"
                self.query = "CREATE INDEX %s ON %s( DISTINCT ARRAY SUBSTR(j.FirstName,8) for j in name end) USING %s" % (
                    idx, bucket.name, self.index_type)
                if self.gsi_type:
                    self.query += " WITH {'index_type': 'memdb'}"
                actual_result = self.run_cbq_query()
                self._wait_for_index_online(bucket, idx)
                self._verify_results(actual_result['results'], [])
                created_indexes.append(idx)
                self.assertTrue(self._is_index_in_list(bucket, idx), "Index is not in list")
                self.query = "explain select name, SUBSTR(name.FirstName, 8) as firstname from %s  where ANY j IN name SATISFIES substr(j.FirstName,8) != 'employee' end" % (bucket.name)
                actual_result = self.run_cbq_query()
                actual_result = self.run_cbq_query()
                actual_result = actual_result['results']
                self.assertTrue(
                    actual_result[0]['~children'][0]['#operator'] == 'UnionScan',
                    "Union Scan is not being used")
                result1 = actual_result[0]['~children'][0]['scans'][0]['index']
                self.assertTrue(result1 == idx)
            finally:
                for idx in created_indexes:
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, idx, self.index_type)
                    actual_result = self.run_cbq_query()
                    self._verify_results(actual_result['results'], [])
                    self.assertFalse(self._is_index_in_list(bucket, idx), "Index is in list")

    def test_array_index_update(self):
        for bucket in self.buckets:
             created_indexes = []
             try:
                idx = "arrayidx_update"
                self.query = "CREATE INDEX %s ON %s( DISTINCT ARRAY ( DISTINCT array j.city for j in i end) FOR i in %s END) USING %s" % (
                    idx, bucket.name, "address", self.index_type)
                if self.gsi_type:
                    self.query += " WITH {'index_type': 'memdb'}"
                actual_result = self.run_cbq_query()
                self._wait_for_index_online(bucket, idx)
                self._verify_results(actual_result['results'], [])
                created_indexes.append(idx)
                self.assertTrue(self._is_index_in_list(bucket, idx), "Index is not in list")
                updated_value = 'new_dept' * 30
                self.query = "explain update %s set name=%s where ANY i IN address SATISFIES  (ANY j IN i SATISFIES j.city='Mumbai' end) END  returning element department"  % (bucket.name, updated_value)
                actual_result = self.run_cbq_query()
                actual_result = (actual_result['results'])
                print actual_result
                self.assertTrue(
                    actual_result[0]['~children'][0]['#operator'] == 'UnionScan',
                    "Union Scan is not being used")
                result1 = actual_result[0]['~children'][0]['scans'][0]['index']
                self.assertTrue(result1 == idx)
                self.query = "update %s set department=%s where ANY i IN address SATISFIES  (ANY j IN i SATISFIES j.city='Mumbai' end) END  returning element department"  % (bucket.name, updated_value)
                self.run_cbq_query()
                self.assertEqual(actual_result['status'], 'success', 'Query was not run successfully')
             finally:
                for idx in created_indexes:
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, idx, self.index_type)
                    actual_result = self.run_cbq_query()
                    self._verify_results(actual_result['results'], [])
                    self.assertFalse(self._is_index_in_list(bucket, idx), "Index is in list")

    def test_array_index_delete(self):
        for bucket in self.buckets:
             created_indexes = []
             try:
                idx = "arrayidx_delete"
                self.query = "CREATE INDEX %s ON %s( DISTINCT ARRAY v FOR v in %s END) USING %s" % (
                    idx, bucket.name, "join_yr", self.index_type)
                if self.gsi_type:
                    self.query += " WITH {'index_type': 'memdb'}"
                create_result = self.run_cbq_query()
                self._wait_for_index_online(bucket, idx)
                self._verify_results(create_result['results'], [])
                created_indexes.append(idx)
                self.assertTrue(self._is_index_in_list(bucket, idx), "Index is not in list")

                self.query = 'select count(*) as actual from %s where join_yr=2012'  % (bucket.name)
                self.run_cbq_query()
                self.sleep(5, 'wait for index')
                actual_result = self.run_cbq_query()
                current_docs = actual_result['results'][0]['actual']
                self.query = 'explain delete from %s where any v in join_yr SATISFIES v=2012 end LIMIT 1'  % (bucket.name)
                actual_result = self.run_cbq_query()
                result = actual_result['results'][0]['~children'][0]['scans'][0]['index']
                self.assertTrue(
                    actual_result['results'][0]['~children'][0]['#operator'] == 'UnionScan',
                    "UnionScan is not being used in and query for 2 array indexes")
                self.assertTrue(result == idx )
                self.query = 'delete from %s where any v in join_yr satisfies v=2012 end LIMIT 1'  % (bucket.name)
                actual_result = self.run_cbq_query()
                self.assertEqual(actual_result['status'], 'success', 'Query was not run successfully')

             finally:
                for idx in created_indexes:
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, idx, self.index_type)
                    actual_result = self.run_cbq_query()
                    self._verify_results(actual_result['results'], [])
                    self.assertFalse(self._is_index_in_list(bucket, idx), "Index is in list")

    def test_simple_create_delete_index(self):
        for bucket in self.buckets:
            created_indexes = []
            try:
                for ind in xrange(self.num_indexes):
                    view_name = "my_index%s" % ind
                    self.query = "CREATE INDEX %s ON %s(%s) USING GSI" % (
                                            view_name, bucket.name, ','.join(self.FIELDS_TO_INDEX[ind - 1]))
                    # if self.gsi_type:
                    #     self.query += " WITH {'index_type': 'memdb'}"
                    actual_result = self.run_cbq_query()
                    import pdb;pdb.set_trace()
                    self._wait_for_index_online(bucket, view_name)
                    self._verify_results(actual_result['results'], [])
                    created_indexes.append(view_name)
                    self.assertTrue(self._is_index_in_list(bucket, view_name), "Index is not in list")
            finally:
                for view_name in created_indexes:
                    self.query = "DROP INDEX %s.%s" % (bucket.name, view_name)
                    actual_result = self.run_cbq_query()
                    self._verify_results(actual_result['results'], [])
                    self.assertFalse(self._is_index_in_list(bucket, view_name), "Index is in list")

    def test_create_delete_index_with_query(self):
        for bucket in self.buckets:
            created_indexes = []
            try:
                for ind in xrange(self.num_indexes):
                    view_name = "tuq_index%s" % ind
                    self.query = "CREATE INDEX %s ON %s(%s)  USING GSI" % (view_name, bucket.name, ','.join(self.FIELDS_TO_INDEX[ind - 1]))
                    # if self.gsi_type:
                    #     self.query += " WITH {'index_type': 'memdb'}"
                    actual_result = self.run_cbq_query()
                    self._wait_for_index_online(bucket, view_name)
                    self._verify_results(actual_result['results'], [])
                    created_indexes.append(view_name)
                    self.query = "select %s from %s where %s is not null and %s is not null" % (','.join(self.FIELDS_TO_INDEX[ind - 1]), bucket.name,
                                                                                                self.FIELDS_TO_INDEX[ind - 1][0], self.FIELDS_TO_INDEX[ind - 1][1])
                    actual_result = self.run_cbq_query()
                    import pdb;pdb.set_trace()
                    self.assertTrue(len(actual_result['results']), self.num_items)
            except Exception, ex:
                content = self.cluster.query_view(self.master, "ddl_%s" % view_name, view_name, {"stale" : "ok"},
                                                  bucket="default", retry_time=1)
                self.log.info("Generated view has %s items" % len(content['rows']))
                raise ex
            finally:
                for view_name in created_indexes:
                    self.query = "DROP INDEX %s.%s" % (bucket.name, view_name)
                    actual_result = self.run_cbq_query()
                    self._verify_results(actual_result['results'], [])
                self.query = "select %s from %s where %s is not null and %s is not null" % (','.join(self.FIELDS_TO_INDEX[ind - 1]), bucket.name,
                                                                                                self.FIELDS_TO_INDEX[ind - 1][0], self.FIELDS_TO_INDEX[ind - 1][1])
                actual_result = self.run_cbq_query(query_params={'scan_consistency' : 'statement_plus'})
                self.assertTrue(len(actual_result['results']), self.num_items)

    def test_explain_query_count(self):
        for bucket in self.buckets:
            index_name = "my_index_child"
            try:
                self.query = "CREATE INDEX %s ON %s(VMs, name)  USING %s" % (index_name, bucket.name,self.index_type)
                # if self.gsi_type:
                #     self.query += " WITH {'index_type': 'memdb'}"
                self.run_cbq_query()
                self._wait_for_index_online(bucket, index_name)
                self.query = 'EXPLAIN SELECT count(VMs) FROM %s where VMs is not null union SELECT count(name) FROM %s where name is not null' % (bucket.name, bucket.name)
                res = self.run_cbq_query()
		plan = ExplainPlanHelper(res)
                self.assertTrue(plan['~children'][0]["children"][0]['~children'][0]["index"] == index_name,
                                "Index should be %s, but is: %s" % (index_name, plan))
            finally:
                self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, index_name,self.index_type)
                try:
                    self.run_cbq_query()
                except:
                    pass

    def test_explain_query_group_by(self):
        for bucket in self.buckets:
            index_name = "my_index_child"
            try:
                self.query = "CREATE INDEX %s ON %s(VMs, join_yr)  USING %s" % (index_name, bucket.name,self.index_type)
                # if self.gsi_type:
                #     self.query += " WITH {'index_type': 'memdb'}"
                self.run_cbq_query()
                self._wait_for_index_online(bucket, index_name)
                self.query = 'EXPLAIN SELECT count(*) FROM %s where VMs is not null GROUP BY VMs, join_yr' % (bucket.name)
                res = self.run_cbq_query()
		plan = ExplainPlanHelper(res)
                self.assertTrue(plan["~children"][0]["index"] == index_name,
                                "Index should be %s, but is: %s" % (index_name, plan))
            finally:
                self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, index_name,self.index_type)
                try:
                    self.run_cbq_query()
                except:
                    pass

    def test_explain_query_meta(self):
        for bucket in self.buckets:
            index_name = "my_index_meta"
            try:
                self.query = "CREATE INDEX %s ON %s(meta(%s).type, name)  USING %s" % (index_name, bucket.name, bucket.name,self.index_type)
                # if self.gsi_type:
                #     self.query += " WITH {'index_type': 'memdb'}"
                self.run_cbq_query()
                self._wait_for_index_online(bucket, index_name)
                self.query = 'EXPLAIN SELECT name FROM %s WHERE meta(%s).type = "json" and name is not null' % (bucket.name, bucket.name)
                res = self.run_cbq_query()
		plan = ExplainPlanHelper(res)
                self.assertTrue(plan["~children"][0]["index"] == index_name,
                                "Index should be %s, but is: %s" % (index_name, plan))
            finally:
                self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, index_name,self.index_type)
                self.run_cbq_query()

    def test_covering_index(self):
        for bucket in self.buckets:
            created_indexes = []
            try:
                for ind in xrange(self.num_indexes):
                    index_name = "coveringindex%s" % ind
                    self.query = "CREATE INDEX %s ON %s(name, join_day)  USING %s" % (index_name, bucket.name,self.index_type)
                    # if self.gsi_type:
                    #     self.query += " WITH {'index_type': 'memdb'}"
                    self.run_cbq_query()
                    self._wait_for_index_online(bucket, index_name)
                    created_indexes.append(index_name)
                    self.query = "EXPLAIN SELECT name, join_day FROM %s where name = 'employee-9'"% (bucket.name)
                    if self.covering_index:
                        self.test_explain_covering_index(index_name)
                        self.query = "EXPLAIN SELECT * FROM %s where name = 'employee-9'"% (bucket.name)
                        res = self.run_cbq_query()
			plan = ExplainPlanHelper(res)
                        self.assertTrue(plan["~children"][0]['index'] == index_name,"correct index is not used")
                    self.query = "SELECT name, join_day FROM %s where name = 'employee-9'"  % (bucket.name)
                    actual_result = self.run_cbq_query()
                    expected_result = [{"name" : doc["name"],"join_day" : doc["join_day"]}
                               for doc in self.full_list
                               if doc['name'] == 'employee-9']
                    expected_result = sorted(expected_result, key=lambda doc: (doc['name']))
                    self._verify_results(actual_result['results'], expected_result)
            finally:
                for index_name in created_indexes:
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, index_name,self.index_type)
                    self.run_cbq_query()
                #self.covering_index = False
                # self.query = "CREATE PRIMARY INDEX ON %s" % bucket.name
                # self.run_cbq_query()
                # self._wait_for_index_online(bucket, '#primary')
                # self.query = "SELECT name, join_day FROM %s where name = 'employee-9'"  % (bucket.name)
                # result = self.run_cbq_query()
                # self.assertTrue(actual_result["metrics"]["elapsedTime"]< result["metrics"]["elapsedTime"],"Time used in queries using covering indexes should be less than time used in queries not using covering indexes")
                # self.query = "DROP PRIMARY INDEX ON %s" % bucket.name
                #self.run_cbq_query()


    def test_covering_partial_index(self):
        for bucket in self.buckets:
            created_indexes = []
            try:
                for ind in xrange(self.num_indexes):
                    index_name = "coveringindexwithwhere%s" % ind
                    self.query = "CREATE INDEX %s ON %s(email, VMs, join_day) where join_day > 10 USING %s" % (index_name, bucket.name,self.index_type)
                    # if self.gsi_type:
                    #     self.query += " WITH {'index_type': 'memdb'}"
                    self.run_cbq_query()
                    self._wait_for_index_online(bucket, index_name)
                    created_indexes.append(index_name)
                    self.query = "explain select email,VMs[0].RAM from %s where email "  % (bucket.name) +\
                                 "LIKE '%@%.%' and VMs[0].RAM > 5 and join_day > 10"
                    if self.covering_index:
                        self.test_explain_covering_index(index_name)
                    self.query = "select email,join_day from %s where email "  % (bucket.name) +\
                                 "LIKE '%@%.%' and VMs[0].RAM > 5 and join_day > 10"
                    actual_result = self.run_cbq_query()
                    expected_result = [{"join_day":doc["join_day"],"email" : doc["email"]}
                               for doc in self.full_list
                               if re.match(r'.*@.*\..*', doc['email']) and \
                                  doc['join_day'] > 10 and \
                                  len([vm for vm in doc["VMs"]
                                        if vm["RAM"] > 5]) > 0]
                    expected_result = sorted(expected_result, key=lambda doc: (doc["join_day"]))
                    self._verify_results(actual_result['results'], expected_result)
            finally:
                for index_name in created_indexes:
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, index_name,self.index_type)
                    self.run_cbq_query()
                    #self.covering_index = False
                    # self.query = "CREATE PRIMARY INDEX ON %s" % bucket.name
                    # self.run_cbq_query()
                    # self._wait_for_index_online(bucket, '#primary')
                    # self.query = "select email,join_day from %s where email "  % (bucket.name) +\
                    #              "LIKE '%@%.%' and VMs[0].RAM > 5 and join_day > 10"
                    # result = self.run_cbq_query()
                    # self.assertTrue(actual_result["metrics"]["elapsedTime"]< result["metrics"]["elapsedTime"],"Time used in queries using covering indexes should be less than time used in queries not using covering indexes")
                    # self.query = "DROP PRIMARY INDEX ON %s" % bucket.name
                    #self.run_cbq_query()

    def test_covering_orderby_limit(self):
        for bucket in self.buckets:
            created_indexes = []
            try:
                for ind in xrange(self.num_indexes):
                    index_name = "coveringindexwithlimit%s" % ind
                    self.query = "CREATE INDEX %s ON %s(skills[0], join_yr, VMs[0].os) where join_yr =2010 USING %s" % (index_name, bucket.name,self.index_type)
                    # if self.gsi_type:
                    #     self.query += " WITH {'index_type': 'memdb'}"
                    self.run_cbq_query()
                    self._wait_for_index_online(bucket, index_name)
                    created_indexes.append(index_name)
                    self.query = "explain select * from %s where skills[0]='skill2010' and join_yr=2010 and VMs[0].os IN ['ubuntu','windows','linux'] order by _id asc LIMIT 10 OFFSET 0;" % (bucket.name)
                    self.test_explain_union(index_name)
                    self.query = "select name,skills[0] as skills from %s where skills[0]='skill2010' and join_yr=2010 and VMs[0].os IN ['ubuntu','windows','linux'] order by name LIMIT 15 OFFSET 0;" % (bucket.name)
                    actual_result = self.run_cbq_query()
                    expected_result = [{"skills" : doc["skills"][0],"name":doc["name"]}
                                       for doc in self.full_list
                                       if doc['join_yr']==2010 and \
                                          doc['skills'][0]=='skill2010' and \
                                          len([vm for vm in doc["VMs"]
                                                if vm["os"] == 'ubuntu' or vm["os"] == 'windows' or vm["os"] == "linux"])>0]
                    expected_result= sorted(expected_result,key=lambda doc: (doc["name"]))[:15]
                    self.max_verify = 15
                    self._verify_results(actual_result['results'], expected_result)
            finally:
                for index_name in created_indexes:
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, index_name,self.index_type)
                    self.run_cbq_query()
                    # self.covering_index = False
                    # self.query = "CREATE PRIMARY INDEX ON %s" % bucket.name
                    # self.run_cbq_query()
                    # self._wait_for_index_online(bucket, '#primary')
                    # self.query = "select name,skills[0] as skills from %s where skills[0]='skill2010' and join_yr=2010 and VMs[0].os IN ['ubuntu','windows','linux'] order by name LIMIT 15 OFFSET 0;"  % (bucket.name)
                    # result = self.run_cbq_query()
                    # self.assertTrue(actual_result["metrics"]["elapsedTime"]< result["metrics"]["elapsedTime"],"Time used in queries using covering indexes should be less than time used in queries not using covering indexes")
                    # self.query = "DROP PRIMARY INDEX ON %s" % bucket.name
                    # self.run_cbq_query()

    def test_covering_groupby(self):
        for bucket in self.buckets:
            created_indexes = []
            try:
                for ind in xrange(self.num_indexes):
                    index_name = "coveringindexwithgroupby%s" % ind
                    self.query = "CREATE INDEX %s ON %s(join_yr,name) where join_yr >2009 USING %s" % (index_name, bucket.name,self.index_type)
                    # if self.gsi_type:
                    #     self.query += " WITH {'index_type': 'memdb'}"
                    self.run_cbq_query()
                    self._wait_for_index_online(bucket, index_name)
                    created_indexes.append(index_name)
                    self.query = "explain SELECT count(*),join_yr FROM %s AS test where join_yr > 2009 GROUP BY join_yr ORDER BY name';" % (bucket.name)
                    if self.covering_index:
                        self.test_explain_covering_index(index_name)
                    self.query = "SELECT count(*),join_yr FROM %s AS test where join_yr > 2009 GROUP BY join_yr ORDER BY name;" % (bucket.name)
                    actual_result = self.run_cbq_query()
                    actual_result = actual_result['results']
                    expected_result = [{"join_yr" : doc["join_yr"]}
                                    for doc in self.full_list
                                    if doc["join_yr"] > 2009]
                    expected_result = [dict(y) for y in set(tuple(x.items()) for x in expected_result)]
                    if len(actual_result) != len(expected_result):
                         missing, extra = self.check_missing_and_extra(actual_result, expected_result)
                         self.log.error("Missing items: %s.\n Extra items: %s" % (missing[:100], extra[:100]))
                         self.fail("Results are incorrect.Actual num %s. Expected num: %s.\n" % (len(actual_result), len(expected_result)))
            finally:
                    for index_name in created_indexes:
                        self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, index_name,self.index_type)
                        self.run_cbq_query()

    def test_covering_groupby_having(self):
        for bucket in self.buckets:
            created_indexes = []
            try:
                for ind in xrange(self.num_indexes):
                    index_name = "coveringindexwithgroupbyhaving%s" % ind
                    self.query = "CREATE INDEX %s ON %s(job_title,join_mo,test_rate) USING %s" % (index_name, bucket.name,self.index_type)
                    # if self.gsi_type:
                    #     self.query += " WITH {'index_type': 'memdb'}"
                    self.run_cbq_query()
                    self._wait_for_index_online(bucket, index_name)
                    created_indexes.append(index_name)
                    self.query = "explain SELECT join_mo, SUM(test_rate) as rate FROM %s " % (bucket.name) +\
                         "as employees WHERE job_title='Engineer' GROUP BY join_mo " +\
                         "HAVING SUM(employees.test_rate) > 0 and " +\
                         "SUM(test_rate) < 100000"
                    if self.covering_index:
                        self.test_explain_covering_index(index_name)
                    self.query = "SELECT join_mo, SUM(test_rate) as rate FROM %s " % (bucket.name) +\
                         "as employees WHERE job_title='Engineer' GROUP BY join_mo " +\
                         "HAVING SUM(employees.test_rate) > 0 and " +\
                         "SUM(test_rate) < 100000"
                    actual_result = self.run_cbq_query()
                    actual_result = [{"join_mo" : doc["join_mo"], "rate" : round(doc["rate"])} for doc in actual_result['results']]
                    actual_result = sorted(actual_result, key=lambda doc: (doc['join_mo']))
                    tmp_groups = set([doc['join_mo'] for doc in self.full_list])
                    expected_result = [{"join_mo" : group,
                                "rate" : round(math.fsum([doc['test_rate']
                                                          for doc in self.full_list
                                                          if doc['join_mo'] == group and\
                                                             doc['job_title'] == 'Engineer']))}
                               for group in tmp_groups
                               if math.fsum([doc['test_rate']
                                            for doc in self.full_list
                                            if doc['join_mo'] == group and\
                                            doc['job_title'] == 'Engineer'] ) > 0 and\
                                  math.fsum([doc['test_rate']
                                            for doc in self.full_list
                                            if doc['join_mo'] == group and\
                                            doc['job_title'] == 'Engineer'] ) < 100000]
                    expected_result = sorted(expected_result, key=lambda doc: (doc['join_mo']))
                    self._verify_results(actual_result, expected_result)
            finally:
                    for index_name in created_indexes:
                        self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, index_name,self.index_type)
                        self.run_cbq_query()

    def test_covering_groupby_letting(self):
        for bucket in self.buckets:
            created_indexes = []
            try:
                for ind in xrange(self.num_indexes):
                    index_name = "coveringindexwithgroupbyletting%s" % ind
                    self.query = "CREATE INDEX %s ON %s(join_mo,test_rate) USING %s" % (index_name, bucket.name,self.index_type)
                    # if self.gsi_type:
                    #     self.query += " WITH {'index_type': 'memdb'}"
                    self.run_cbq_query()
                    self._wait_for_index_online(bucket, index_name)
                    created_indexes.append(index_name)
                    self.query = "explain SELECT join_mo, sum_test from %s WHERE join_mo>7 group by join_mo letting sum_test = sum(test_rate) " % (bucket.name)
                    if self.covering_index:
                        self.test_explain_covering_index(index_name)
                    self.query = "SELECT join_mo, sum_test from %s WHERE join_mo>7 group by join_mo letting sum_test = sum(test_rate)" % (bucket.name)
                    actual_list = self.run_cbq_query()
                    actual_result = sorted(actual_list['results'])
                    tmp_groups = set([doc['join_mo'] for doc in self.full_list if doc['join_mo']>7])
                    expected_result = [{"join_mo" : group,
                              "sum_test" : sum([x["test_rate"] for x in self.full_list
                                               if x["join_mo"] == group])}
                               for group in tmp_groups]
                    expected_result = sorted(expected_result)
                    self._verify_results(actual_result, expected_result)
            finally:
                    for index_name in created_indexes:
                        self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, index_name,self.index_type)
                        self.run_cbq_query()

    def test_covering_index_hints_select_explain(self):
        index_name_prefix_list = ['hint' + str(uuid.uuid4())[:4],'covering_hint' + str(uuid.uuid4())[:4]]
        created_indexes = []
        for bucket in self.buckets:
            for ind in index_name_prefix_list:
                    for attr in ['join_day', 'join_mo']:
                        ind_name = '%s_%s' % (ind, attr)
                        self.query = "CREATE INDEX %s ON %s(%s)  USING %s" % (ind_name,
                                                                    bucket.name, attr, self.index_type)
                        # if self.gsi_type:
                        #     self.query += " WITH {'index_type': 'memdb'}"
                        self.run_cbq_query()
                        self._wait_for_index_online(bucket, ind_name)
                        created_indexes.append('%s' % (ind_name))
        for bucket in self.buckets:
                try:
                    for ind in created_indexes:
                        if "covering" in ind:
                                if "join_day" in ind:
                                    self.query = 'EXPLAIN SELECT join_day FROM %s  USE INDEX(%s using %s) WHERE join_day>2' % (bucket.name, ind, self.index_type)
                                    self.test_explain_covering_index(ind)
                                elif "join_mo" in ind:
                                    self.query = 'EXPLAIN SELECT join_mo FROM %s  USE INDEX(%s using %s) WHERE join_mo>3' % (bucket.name, ind, self.index_type)
                                    self.test_explain_covering_index(ind)
                        else:
                            self.query = 'EXPLAIN SELECT name,join_day, join_mo FROM %s  USE INDEX(%s using %s) WHERE join_day>2 AND join_mo>3' % (bucket.name, ind, self.index_type)
                            res = self.run_cbq_query()
			    plan = ExplainPlanHelper(res)
                            self.assertTrue([plan["~children"][0]["index"] == ind,
                                    "Index should be %s, but is: %s" % (ind, plan["~children"][0]["index"]))

                finally:
                     for index_name in set(created_indexes):
                        self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, index_name,self.index_type)
                        self.run_cbq_query()

    def async_monitor_index(self, bucket, index_name = None):
        monitor_index_task = self.cluster.async_monitor_index(
                 server = self.n1ql_server, bucket = bucket,
                 n1ql_helper = self.n1ql_helper,
                 index_name = index_name)
        return monitor_index_task

##############################################################################################
#
#   SCALAR FN
##############################################################################################

    def test_ceil_covering_index(self):
        for bucket in self.buckets:
            created_indexes = []
            for ind in xrange(self.num_indexes):
                    index_name = "coveringindexwithceil%s" % ind
                    self.query = "CREATE INDEX %s ON %s(name,test_rate)  USING %s" % (index_name, bucket.name,self.index_type)
                    # if self.gsi_type:
                    #     self.query += " WITH {'index_type': 'memdb'}"
                    self.run_cbq_query()
                    created_indexes.append(index_name)
        for bucket in self.buckets:
                tasks=[]
                for index_name in created_indexes:
                    try:
                        tasks.append(self.async_monitor_index(bucket = bucket.name, index_name = index_name))
                        for task in tasks:
                            task.result()
                    except Exception, ex:
                        self.log.info(ex)


                self.query = "explain select name,ceil(test_rate) as rate from %s where name LIKE '%s' and ceil(test_rate) > 5"  % (bucket.name,"employee")
                if self.covering_index:
                    self.test_explain_covering_index(index_name)
                self.query = "select test_rate from %s where name = 'employee-9' and ceil(test_rate) > 5"  % (bucket.name)
                actual_result = self.run_cbq_query()
                actual_result = sorted(actual_result['results'], key=lambda doc: (doc['test_rate']))
                expected_result = [{"test_rate" : doc['test_rate']} for doc in self.full_list
                                   if doc['name'] == 'employee-9' and
                                   math.ceil(doc['test_rate']) > 5]
                expected_result = sorted(expected_result, key=lambda doc: (doc['test_rate']))
                self._verify_results(actual_result, expected_result)
                for index_name in set(created_indexes):
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, index_name,self.index_type)
                    self.run_cbq_query()

    def test_floor_covering_index(self):
        for bucket in self.buckets:
            created_indexes = []
            for ind in xrange(self.num_indexes):
                    index_name = "coveringindexwithfloor%s" % ind
                    self.query = "CREATE INDEX %s ON %s(name,test_rate)  USING %s" % (index_name, bucket.name,self.index_type)
                    # if self.gsi_type:
                    #     self.query += " WITH {'index_type': 'memdb'}"
                    self.run_cbq_query()
                    created_indexes.append(index_name)
        for bucket in self.buckets:
            try:
                tasks=[]
                for index_name in created_indexes:
                    try:
                        tasks.append(self.async_monitor_index(bucket = bucket.name, index_name = index_name))
                        for task in tasks:
                            task.result()
                    except Exception, ex:
                        self.log.info(ex)

                self.query = "explain select name,floor(test_rate) from %s where name LIKE '%s' and floor(test_rate) > 5"  % (bucket.name,"employee")
                if self.covering_index:
                    self.test_explain_covering_index(index_name)

                self.query = "select test_rate from %s where name = 'employee-9' and floor(test_rate) > 5"  % (bucket.name)
                actual_result = self.run_cbq_query()
                actual_result = sorted(actual_result['results'], key=lambda doc: (doc['test_rate']))
                expected_result = [{"test_rate" : doc['test_rate']} for doc in self.full_list
                                   if doc['name'] == 'employee-9' and
                                   math.floor(doc['test_rate']) > 5]
                expected_result = sorted(expected_result, key=lambda doc: (doc['test_rate']))
                self._verify_results(actual_result, expected_result)
            finally:
                for index_name in set(created_indexes):
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, index_name,self.index_type)
                    self.run_cbq_query()

    def test_greatest_covering_index(self):
        for bucket in self.buckets:
            created_indexes = []
            for ind in xrange(self.num_indexes):
                    index_name = "coveringindexwithgreatest%s" % ind
                    self.query = "CREATE INDEX %s ON %s(name,skills[0], skills[1])  USING %s" % (index_name, bucket.name,self.index_type)
                    # if self.gsi_type:
                    #     self.query += " WITH {'index_type': 'memdb'}"
                    self.run_cbq_query()
                    created_indexes.append(index_name)
        for bucket in self.buckets:
                tasks=[]
                for index_name in created_indexes:
                    try:
                        tasks.append(self.async_monitor_index(bucket = bucket.name, index_name = index_name))
                        for task in tasks:
                            task.result()
                    except Exception, ex:
                        self.log.info(ex)

        for bucket in self.buckets:
            try:
                for index_name in created_indexes:
                    self.query = "explain select GREATEST(skills[0], skills[1]) as SKILL from %s where name LIKE '%s' and skills[0]='skill2010'"  % (bucket.name,"employee")
                if self.covering_index:
                    self.test_explain_covering_index(index_name)

                self.query = "select GREATEST(skills[0], skills[1]) as SKILL from %s where name = 'employee-9' and skills[0]='skill2010'"  % (
                    bucket.name)
                actual_result = self.run_cbq_query()
                actual_result = sorted(actual_result['results'],
                                       key=lambda doc: (doc['SKILL']))

                expected_result = [{"SKILL" :
                                        (doc['skills'][0], doc['skills'][1])[doc['skills'][0]<doc['skills'][1]]}
                                   for doc in self.full_list
                                   if doc['skills'][0]=='skill2010' and doc['name']=='employee-9']
                expected_result = sorted(expected_result, key=lambda doc: (doc['SKILL']))
                self._verify_results(actual_result, expected_result)
            finally:
                for index_name in set(created_indexes):
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, index_name,self.index_type)
                    self.run_cbq_query()

    def test_least_covering_index(self):
        for bucket in self.buckets:
            created_indexes = []
            for ind in xrange(self.num_indexes):
                    index_name = "coveringindexwithleast%s" % ind
                    self.query = "CREATE INDEX %s ON %s(name,skills[0], skills[1])  USING %s" % (index_name, bucket.name,self.index_type)
                    # if self.gsi_type:
                    #     self.query += " WITH {'index_type': 'memdb'}"
                    self.run_cbq_query()
                    created_indexes.append(index_name)

        for bucket in self.buckets:
                tasks=[]
                for index_name in created_indexes:
                    try:
                        tasks.append(self.async_monitor_index(bucket = bucket.name, index_name = index_name))
                        for task in tasks:
                            task.result()
                    except Exception, ex:
                        self.log.info(ex)

        for bucket in self.buckets:
            try:
                for index_name in created_indexes:
                    self.query = "explain select LEAST(skills[0], skills[1]) as SKILL from %s where name LIKE '%s' and skills[0]='skill2010'"  % (bucket.name,"employee")
                    if self.covering_index:
                        self.test_explain_covering_index(index_name)
                    self.query = "select LEAST(skills[0], skills[1]) as SKILL from %s where name = 'employee-9' and skills[0]='skill2010'"  % (
                        bucket.name)
                    actual_result = self.run_cbq_query()
                    actual_result = sorted(actual_result['results'],
                                           key=lambda doc: (doc['SKILL']))

                    expected_result = [{"SKILL" :
                                            (doc['skills'][0], doc['skills'][1])[doc['skills'][0]>doc['skills'][1]]}
                                       for doc in self.full_list
                                       if doc['skills'][0]=='skill2010' and doc['name']=='employee-9']
                    expected_result = sorted(expected_result, key=lambda doc: (doc['SKILL']))
                    self._verify_results(actual_result, expected_result)
            finally:
                for index_name in set(created_indexes):
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, index_name,self.index_type)
                    self.run_cbq_query()

##############################################################################################
#
#   AGGR FN
##############################################################################################

    def test_min_covering_index(self):
        for bucket in self.buckets:
            created_indexes = []
            for ind in xrange(self.num_indexes):
                    index_name = "coveringindexwithmin%s" % ind
                    self.query = "CREATE INDEX %s ON %s(job_title,join_mo,test_rate)  USING %s" % (index_name, bucket.name,self.index_type)
                    # if self.gsi_type:
                    #     self.query += " WITH {'index_type': 'memdb'}"
                    self.run_cbq_query()
                    created_indexes.append(index_name)

        for bucket in self.buckets:
                tasks=[]
                for index_name in created_indexes:
                    try:
                        tasks.append(self.async_monitor_index(bucket = bucket.name, index_name = index_name))
                        for task in tasks:
                            task.result()
                    except Exception, ex:
                        self.log.info(ex)

        for bucket in self.buckets:
            try:
                for index_name in created_indexes:
                    self.query = "explain SELECT join_mo, MIN(test_rate) as rate FROM %s " % (bucket.name) + \
                                 "as employees WHERE job_title='Sales' GROUP BY join_mo " + \
                                 "HAVING MIN(employees.test_rate) > 0 and " + \
                                 "SUM(test_rate) < 100000"
                    if self.covering_index:
                        self.test_explain_covering_index(index_name)

                    self.query = "SELECT join_mo, MIN(test_rate) as rate FROM %s " % (bucket.name) + \
                                 "as employees WHERE job_title='Sales' GROUP BY join_mo " + \
                                 "HAVING MIN(employees.test_rate) > 0 and " + \
                                 "SUM(test_rate) < 100000"

                    actual_result = self.run_cbq_query()
                    actual_result = sorted(actual_result['results'], key=lambda doc: (doc['join_mo']))
                    tmp_groups = set([doc['join_mo'] for doc in self.full_list])
                    expected_result = [{"join_mo" : group,
                                        "rate" : min([doc['test_rate']
                                                      for doc in self.full_list
                                                      if doc['join_mo'] == group and \
                                                      doc['job_title'] == 'Sales'])}
                                       for group in tmp_groups
                                       if min([doc['test_rate']
                                               for doc in self.full_list
                                               if doc['join_mo'] == group and \
                                               doc['job_title'] == 'Sales']) > 0 and \
                                       math.fsum([doc['test_rate']
                                                  for doc in self.full_list
                                                  if doc['join_mo'] == group and \
                                                  doc['job_title'] == 'Sales']) < 100000]
                    expected_result = sorted(expected_result, key=lambda doc: (doc['join_mo']))
                    self._verify_results(actual_result, expected_result)
            finally:
                for index_name in set(created_indexes):
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, index_name,self.index_type)
                    self.run_cbq_query()

    def test_max_covering_index(self):
        for bucket in self.buckets:
            created_indexes = []
            for ind in xrange(self.num_indexes):
                    index_name = "coveringindexwithmax%s" % ind
                    self.query = "CREATE INDEX %s ON %s(job_title,join_mo,test_rate)  USING %s" % (index_name, bucket.name,self.index_type)
                    # if self.gsi_type:
                    #     self.query += " WITH {'index_type': 'memdb'}"
                    self.run_cbq_query()
                    created_indexes.append(index_name)

        for bucket in self.buckets:
                tasks=[]
                for index_name in created_indexes:
                    try:
                        tasks.append(self.async_monitor_index(bucket = bucket.name, index_name = index_name))
                        for task in tasks:
                            task.result()
                    except Exception, ex:
                        self.log.info(ex)
        for bucket in self.buckets:
            try:
                for index_name in created_indexes:
                    self.query = "explain SELECT join_mo, MAX(test_rate) as rate FROM %s " % (bucket.name) + \
                                 "as employees WHERE job_title='Sales' GROUP BY join_mo " + \
                                 "HAVING MAX(employees.test_rate) > 0 and " + \
                                 "SUM(test_rate) < 100000"

                    if self.covering_index:
                        self.test_explain_covering_index(index_name)


                    self.query = "SELECT join_mo, MAX(test_rate) as rate FROM %s " % (bucket.name) + \
                                 "as employees WHERE job_title='Sales' GROUP BY join_mo " + \
                                 "HAVING MAX(employees.test_rate) > 0 and " + \
                                 "SUM(test_rate) < 100000"

                    actual_result = self.run_cbq_query()
                    actual_result = sorted(actual_result['results'], key=lambda doc: (doc['join_mo']))
                    tmp_groups = set([doc['join_mo'] for doc in self.full_list])
                    expected_result = [{"join_mo" : group,
                                        "rate" : max([doc['test_rate']
                                                      for doc in self.full_list
                                                      if doc['join_mo'] == group and \
                                                      doc['job_title'] == 'Sales'])}
                                       for group in tmp_groups
                                       if max([doc['test_rate']
                                               for doc in self.full_list
                                               if doc['join_mo'] == group and \
                                               doc['job_title'] == 'Sales'] ) > 0 and \
                                       math.fsum([doc['test_rate']
                                                  for doc in self.full_list
                                                  if doc['join_mo'] == group and \
                                                  doc['job_title'] == 'Sales'] ) < 100000]
                    expected_result = sorted(expected_result, key=lambda doc: (doc['join_mo']))
                    self._verify_results(actual_result, expected_result)
            finally:
                for index_name in set(created_indexes):
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, index_name,self.index_type)
                    self.run_cbq_query()

    def test_array_agg_distinct_covering_index(self):
        for bucket in self.buckets:
            created_indexes = []
            for ind in xrange(self.num_indexes):
                    index_name = "coveringindexwitharraydistinct%s" % ind
                    self.query = "CREATE INDEX %s ON %s(join_mo,job_title,test_rate,name)  USING %s" % (index_name, bucket.name,self.index_type)
                    # if self.gsi_type:
                    #     self.query += " WITH {'index_type': 'memdb'}"
                    self.run_cbq_query()
                    created_indexes.append(index_name)

        for bucket in self.buckets:
                tasks=[]
                for index_name in created_indexes:
                    try:
                        tasks.append(self.async_monitor_index(bucket = bucket.name, index_name = index_name))
                        for task in tasks:
                            task.result()
                    except Exception, ex:
                        self.log.info(ex)
        for bucket in self.buckets:
            try:
                for index_name in created_indexes:
                    self.query = "explain SELECT job_title, array_agg(DISTINCT name) as names" + \
                                 " FROM %s  WHERE join_mo=7 GROUP BY job_title" % (bucket.name)

                    if self.covering_index:
                        self.test_explain_covering_index(index_name)

                    self.query = "SELECT job_title, array_agg(DISTINCT name) as names" + \
                                 " FROM %s WHERE join_mo=7 GROUP BY job_title" % (bucket.name)

                    actual_list = self.run_cbq_query()
                    actual_result = self.sort_nested_list(actual_list['results'])
                    actual_result = sorted(actual_result, key=lambda doc: (doc['job_title']))

                    tmp_groups = set([doc['job_title'] for doc in self.full_list if doc['join_mo']==7])
                    expected_list = [{"job_title" : group,
                                      "names" : set([x["name"] for x in self.full_list
                                                     if x["job_title"] == group])}
                                     for group in tmp_groups]
                    expected_result = self.sort_nested_list(expected_list)
                    expected_result = sorted(expected_result, key=lambda doc: (doc['job_title']))
                    self._verify_results(actual_result, expected_result)
            finally:
                for index_name in set(created_indexes):
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, index_name,self.index_type)
                    self.run_cbq_query()

    def test_array_length_covering_index(self):
        for bucket in self.buckets:
            created_indexes = []
            for ind in xrange(self.num_indexes):
                    index_name = "coveringindexwitharraylength%s" % ind
                    self.query = "CREATE INDEX %s ON %s(join_mo,job_title,name)  USING %s" % (index_name, bucket.name,self.index_type)
                    # if self.gsi_type:
                    #     self.query += " WITH {'index_type': 'memdb'}"
                    self.run_cbq_query()
                    created_indexes.append(index_name)

        for bucket in self.buckets:
                tasks=[]
                for index_name in created_indexes:
                    try:
                        tasks.append(self.async_monitor_index(bucket = bucket.name, index_name = index_name))
                        for task in tasks:
                            task.result()
                    except Exception, ex:
                        self.log.info(ex)
        for bucket in self.buckets:
            try:
                for index_name in created_indexes:
                    self.query = "explain SELECT job_title, array_length(array_agg(DISTINCT name)) as num_names" + \
                                 " FROM %s WHERE join_mo=7 GROUP BY job_title" % (bucket.name)

                    if self.covering_index:
                        self.test_explain_covering_index(index_name)

                    self.query = "SELECT job_title, array_length(array_agg(DISTINCT name)) as num_names" + \
                                 " FROM %s WHERE join_mo=7 GROUP BY job_title" % (bucket.name)

                    actual_result = self.run_cbq_query()
                    actual_result = sorted(actual_result['results'], key=lambda doc: (doc['job_title']))

                    tmp_groups = set([doc['job_title'] for doc in self.full_list if doc['join_mo']==7])
                    expected_result = [{"job_title" : group,
                                        "num_names" : len(set([x["name"] for x in self.full_list
                                                               if x["job_title"] == group]))}
                                       for group in tmp_groups]

                    expected_result = sorted(expected_result, key=lambda doc: (doc['job_title']))
                    self._verify_results(actual_result, expected_result)
            finally:
                for index_name in set(created_indexes):
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, index_name,self.index_type)
                    self.run_cbq_query()

    def test_array_append_covering_index(self):
        for bucket in self.buckets:
            created_indexes = []
            for ind in xrange(self.num_indexes):
                    index_name = "coveringindexwitharrayappend%s" % ind
                    self.query = "CREATE INDEX %s ON %s(join_mo,job_title,name) USING %s" % (index_name, bucket.name,self.index_type)
                    # if self.gsi_type:
                    #     self.query += " WITH {'index_type': 'memdb'}"
                    self.run_cbq_query()
                    created_indexes.append(index_name)

        for bucket in self.buckets:
            tasks=[]
            for index_name in created_indexes:
                try:
                    tasks.append(self.async_monitor_index(bucket = bucket.name, index_name = index_name))
                    for task in tasks:
                        task.result()
                except Exception, ex:
                    self.log.info(ex)

        for bucket in self.buckets:
            try:
                for index_name in created_indexes:
                    self.query = "explain SELECT job_title," + \
                                 " array_append(array_agg(DISTINCT name), 'new_name') as names" + \
                                 " FROM %s WHERE join_mo=7 GROUP BY job_title" % (bucket.name)
                    if self.covering_index:
                        self.test_explain_covering_index(index_name)

                    self.query = "SELECT job_title," + \
                                 " array_append(array_agg(DISTINCT name), 'new_name') as names" + \
                                 " FROM %s WHERE join_mo=7 GROUP BY job_title" % (bucket.name)

                    actual_list = self.run_cbq_query()
                    actual_result = self.sort_nested_list(actual_list['results'])
                    actual_result = sorted(actual_result, key=lambda doc: (doc['job_title']))

                    tmp_groups = set([doc['job_title'] for doc in self.full_list if doc['join_mo']==7])
                    expected_result = [{"job_title" : group,
                                        "names" : sorted(set([x["name"] for x in self.full_list
                                                              if x["job_title"] == group] + ['new_name']))}
                                       for group in tmp_groups]
                    expected_result = sorted(expected_result, key=lambda doc: (doc['job_title']))
                    self._verify_results(actual_result, expected_result)
            finally:
                for index_name in set(created_indexes):
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, index_name,self.index_type)
                    self.run_cbq_query()




    def test_array_concat_covering_index(self):
        for bucket in self.buckets:
            created_indexes = []
            for ind in xrange(self.num_indexes):
                    index_name = "coveringindexwitharrayconcat%s" % ind
                    self.query = "CREATE INDEX %s ON %s(join_mo,job_title,name,email)  USING %s" % (index_name, bucket.name,self.index_type)
                    # if self.gsi_type:
                    #     self.query += " WITH {'index_type': 'memdb'}"
                    self.run_cbq_query()
                    created_indexes.append(index_name)

        for bucket in self.buckets:
            tasks=[]
            for index_name in created_indexes:
                try:
                    tasks.append(self.async_monitor_index(bucket = bucket.name, index_name = index_name))
                    for task in tasks:
                        task.result()
                except Exception, ex:
                    self.log.info(ex)

        for bucket in self.buckets:
            try:
                for index_name in created_indexes:
                    self.query = "explain SELECT job_title," + \
                                 " array_concat(array_agg(name), array_agg(email)) as names" + \
                                 " FROM %s WHERE join_mo=7  GROUP BY job_title" % (bucket.name)
                    if self.covering_index:
                        self.test_explain_covering_index(index_name)
            finally:
                for index_name in set(created_indexes):
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, index_name,self.index_type)
                    self.run_cbq_query()


    def test_array_prepend_covering_index(self):
        for bucket in self.buckets:
            created_indexes = []
            for ind in xrange(self.num_indexes):
                    index_name = "coveringindexwitharrayprepend%s" % ind
                    self.query = "CREATE INDEX %s ON %s(join_mo,job_title,test_rate)  USING %s" % (index_name, bucket.name,self.index_type)
                    # if self.gsi_type:
                    #     self.query += " WITH {'index_type': 'memdb'}"
                    self.run_cbq_query()
                    created_indexes.append(index_name)

        for bucket in self.buckets:
            tasks=[]
            for index_name in created_indexes:
                try:
                    tasks.append(self.async_monitor_index(bucket = bucket.name, index_name = index_name))
                    for task in tasks:
                        task.result()
                except Exception, ex:
                    self.log.info(ex)


        for bucket in self.buckets:
            try:
                for index_name in created_indexes:
                    self.query = "explain SELECT job_title," + \
                                 " array_prepend(1.2, array_agg(test_rate)) as rates" + \
                                 " FROM %s WHERE join_mo=7  GROUP BY job_title" % (bucket.name)
                    if self.covering_index:
                        self.test_explain_covering_index(index_name)
            finally:
                for index_name in set(created_indexes):
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, index_name,self.index_type)
                    self.run_cbq_query()


    def test_array_remove_covering_index(self):
        for bucket in self.buckets:
            created_indexes = []
            for ind in xrange(self.num_indexes):
                    index_name = "coveringindexwitharrayremove%s" % ind
                    self.query = "CREATE INDEX %s ON %s(join_mo,job_title,name)  USING %s" % (index_name, bucket.name,self.index_type)
                    # if self.gsi_type:
                    #     self.query += " WITH {'index_type': 'memdb'}"
                    self.run_cbq_query()
                    created_indexes.append(index_name)

        for bucket in self.buckets:
            tasks=[]
            for index_name in created_indexes:
                try:
                    tasks.append(self.async_monitor_index(bucket = bucket.name, index_name = index_name))
                    for task in tasks:
                        task.result()
                except Exception, ex:
                    self.log.info(ex)


        value = 'employee-1'
        for bucket in self.buckets:
            try:
                for index_name in created_indexes:
                    self.query = "explain SELECT job_title," + \
                                 " array_remove(array_agg(DISTINCT name), '%s') as names" % (value) + \
                                 " FROM %s WHERE join_mo=7  GROUP BY job_title" % (bucket.name)
                    if self.covering_index:
                        self.test_explain_covering_index(index_name)

                    self.query = "SELECT job_title," + \
                                 " array_remove(array_agg(DISTINCT name), '%s') as names" % (value) + \
                                 " FROM %s WHERE join_mo=7  GROUP BY job_title" % (bucket.name)

                    actual_list = self.run_cbq_query()
                    actual_result = self.sort_nested_list(actual_list['results'])
                    actual_result = sorted(actual_result, key=lambda doc: (doc['job_title']))

                    tmp_groups = set([doc['job_title'] for doc in self.full_list if doc['join_mo']==7])
                    expected_result = [{"job_title" : group,
                                        "names" : sorted(set([x["name"] for x in self.full_list
                                                              if x["job_title"] == group and x["name"]!= value]))}
                                       for group in tmp_groups]
                    expected_result = sorted(expected_result, key=lambda doc: (doc['job_title']))
                    self._verify_results(actual_result, expected_result)
            finally:
                for index_name in set(created_indexes):
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, index_name,self.index_type)
                    self.run_cbq_query()


    def test_array_avg_covering_index(self):
        for bucket in self.buckets:
            created_indexes = []
            for ind in xrange(self.num_indexes):
                    index_name = "coveringindexwitharrayavg%s" % ind
                    self.query = "CREATE INDEX %s ON %s(join_mo,job_title,test_rate)  USING %s" % (index_name, bucket.name,self.index_type)
                    # if self.gsi_type:
                    #     self.query += " WITH {'index_type': 'memdb'}"
                    self.run_cbq_query()
                    created_indexes.append(index_name)

        for bucket in self.buckets:
            tasks=[]
            for index_name in created_indexes:
                try:
                    tasks.append(self.async_monitor_index(bucket = bucket.name, index_name = index_name))
                    for task in tasks:
                        task.result()
                except Exception, ex:
                    self.log.info(ex)
        for bucket in self.buckets:
            try:
                for index_name in created_indexes:
                    self.query = "explain SELECT job_title, array_avg(array_agg(test_rate))" + \
                                 " as rates FROM %s  WHERE join_mo=7  GROUP BY job_title" % (bucket.name)

                    if self.covering_index:
                        self.test_explain_covering_index(index_name)
            finally:
                for index_name in set(created_indexes):
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, index_name,self.index_type)
                    self.run_cbq_query()


    def test_array_contains_covering_index(self):
        for bucket in self.buckets:
            created_indexes = []
            for ind in xrange(self.num_indexes):
                    index_name = "coveringindexwitharraycontains%s" % ind
                    self.query = "CREATE INDEX %s ON %s(join_mo,job_title,name)  USING %s" % (index_name, bucket.name,self.index_type)
                    # if self.gsi_type:
                    #     self.query += " WITH {'index_type': 'memdb'}"
                    self.run_cbq_query()
                    created_indexes.append(index_name)

        for bucket in self.buckets:
            tasks=[]
            for index_name in created_indexes:
                try:
                    tasks.append(self.async_monitor_index(bucket = bucket.name, index_name = index_name))
                    for task in tasks:
                        task.result()
                except Exception, ex:
                    self.log.info(ex)

        for bucket in self.buckets:
            try:
                for index_name in created_indexes:
                    self.query = "explain SELECT job_title, array_contains(array_agg(name), 'employee-1')" + \
                                 " as emp_job FROM %s  WHERE join_mo=7  GROUP BY job_title" % (bucket.name)

                    if self.covering_index:
                        self.test_explain_covering_index(index_name)

                    self.query = "SELECT job_title, array_contains(array_agg(name), 'employee-1')" + \
                                 " as emp_job FROM %s  WHERE join_mo=7  GROUP BY job_title" % (bucket.name)

                    actual_result = self.run_cbq_query()
                    actual_result = sorted(actual_result['results'])

                    tmp_groups = set([doc['job_title'] for doc in self.full_list if doc['join_mo']==7])
                    expected_result = [{"job_title" : group,
                                        "emp_job" : 'employee-1' in [x["name"] for x in self.full_list
                                                                     if x["job_title"] == group] }
                                       for group in tmp_groups]
                    expected_result = sorted(expected_result)
                    self._verify_results(actual_result, expected_result)
            finally:
                for index_name in set(created_indexes):
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, index_name,self.index_type)
                    self.run_cbq_query()

    def test_array_count_covering_index(self):
        for bucket in self.buckets:
            created_indexes = []
            for ind in xrange(self.num_indexes):
                    index_name = "coveringindexwitharraycount%s" % ind
                    self.query = "CREATE INDEX %s ON %s(join_mo,job_title,name)  USING %s" % (index_name, bucket.name,self.index_type)
                    # if self.gsi_type:
                    #     self.query += " WITH {'index_type': 'memdb'}"
                    self.run_cbq_query()
                    created_indexes.append(index_name)

        for bucket in self.buckets:
            tasks=[]
            for index_name in created_indexes:
                try:
                    tasks.append(self.async_monitor_index(bucket = bucket.name, index_name = index_name))
                    for task in tasks:
                        task.result()
                except Exception, ex:
                    self.log.info(ex)

        for bucket in self.buckets:
            try:
                for index_name in created_indexes:
                    self.query = "explain SELECT job_title, array_count(array_agg(name)) as names" + \
                                 " FROM %s WHERE join_mo=7  GROUP BY job_title" % (bucket.name)

                if self.covering_index:
                    self.test_explain_covering_index(index_name)
            finally:
                for index_name in set(created_indexes):
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, index_name,self.index_type)
                    self.run_cbq_query()


    def test_array_distinct_covering_indexes(self):
        for bucket in self.buckets:
            created_indexes = []
            for ind in xrange(self.num_indexes):
                    index_name = "coveringindexwitharraydistinct%s" % ind
                    self.query = "CREATE INDEX %s ON %s(join_mo,job_title,name)  USING %s" % (index_name, bucket.name,self.index_type)
                    # if self.gsi_type:
                    #     self.query += " WITH {'index_type': 'memdb'}"
                    self.run_cbq_query()
                    created_indexes.append(index_name)

        for bucket in self.buckets:
            tasks=[]
            for index_name in created_indexes:
                try:
                    tasks.append(self.async_monitor_index(bucket = bucket.name, index_name = index_name))
                    for task in tasks:
                        task.result()
                except Exception, ex:
                    self.log.info(ex)

        for bucket in self.buckets:
            try:
                for index_name in created_indexes:
                    self.query = "explain SELECT job_title, array_distinct(array_agg(name)) as names" + \
                                 " FROM %s WHERE join_mo=7 GROUP BY job_title" % (bucket.name)

                    if self.covering_index:
                        self.test_explain_covering_index(index_name)


                    self.query = "SELECT job_title, array_distinct(array_agg(name)) as names" + \
                                 " FROM %s WHERE join_mo=7 GROUP BY job_title" % (bucket.name)

                    actual_list = self.run_cbq_query()
                    actual_result = self.sort_nested_list(actual_list['results'])
                    actual_result = sorted(actual_result, key=lambda doc: (doc['job_title']))

                    tmp_groups = set([doc['job_title'] for doc in self.full_list if doc['join_mo']==7])
                    expected_result = [{"job_title" : group,
                                        "names" : sorted(set([x["name"] for x in self.full_list
                                                              if x["job_title"] == group]))}
                                       for group in tmp_groups]
                    expected_result = sorted(expected_result, key=lambda doc: (doc['job_title']))
                    self._verify_results(actual_result, expected_result)
            finally:
                for index_name in set(created_indexes):
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, index_name,self.index_type)
                    self.run_cbq_query()

    def test_array_max_covering_index(self):
        for bucket in self.buckets:
            created_indexes = []
            for ind in xrange(self.num_indexes):
                    index_name = "coveringindexwitharraymax%s" % ind
                    self.query = "CREATE INDEX %s ON %s(join_mo,job_title,test_rate)  USING %s" % (index_name, bucket.name,self.index_type)
                    # if self.gsi_type:
                    #     self.query += " WITH {'index_type': 'memdb'}"
                    self.run_cbq_query()
                    created_indexes.append(index_name)

        for bucket in self.buckets:
            tasks=[]
            for index_name in created_indexes:
                try:
                    tasks.append(self.async_monitor_index(bucket = bucket.name, index_name = index_name))
                    for task in tasks:
                        task.result()
                except Exception, ex:
                    self.log.info(ex)

        for bucket in self.buckets:
            try:
                for index_name in created_indexes:
                    self.query = "explain SELECT job_title, array_max(array_agg(test_rate)) as rates" + \
                                 " FROM %s WHERE join_mo=7 GROUP BY job_title" % (bucket.name)

                    if self.covering_index:
                        self.test_explain_covering_index(index_name)
            finally:
                for index_name in set(created_indexes):
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, index_name,self.index_type)
                    self.run_cbq_query()


    def test_array_sum_covering_index(self):
        for bucket in self.buckets:
            created_indexes = []
            for ind in xrange(self.num_indexes):
                    index_name = "coveringindexwitharraysum%s" % ind
                    self.query = "CREATE INDEX %s ON %s(join_mo,job_title,test_rate)  USING %s" % (index_name, bucket.name,self.index_type)
                    # if self.gsi_type:
                    #     self.query += " WITH {'index_type': 'memdb'}"
                    self.run_cbq_query()
                    created_indexes.append(index_name)

        for bucket in self.buckets:
            tasks=[]
            for index_name in created_indexes:
                try:
                    tasks.append(self.async_monitor_index(bucket = bucket.name, index_name = index_name))
                    for task in tasks:
                        task.result()
                except Exception, ex:
                    self.log.info(ex)

        for bucket in self.buckets:
            try:
                for index_name in created_indexes:
                    self.query = "explain SELECT job_title, round(array_sum(array_agg(test_rate))) as rates" + \
                                 " FROM %s WHERE join_mo=7 GROUP BY job_title" % (bucket.name)

                    if self.covering_index:
                        self.test_explain_covering_index(index_name)
            finally:
                for index_name in set(created_indexes):
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, index_name,self.index_type)
                    self.run_cbq_query()

    def test_array_min_covering_index(self):
        for bucket in self.buckets:
            created_indexes = []
            for ind in xrange(self.num_indexes):
                    index_name = "coveringindexwitharraymin%s" % ind
                    self.query = "CREATE INDEX %s ON %s(join_mo,job_title,test_rate)  USING %s" % (index_name, bucket.name,self.index_type)
                    # if self.gsi_type:
                    #     self.query += " WITH {'index_type': 'memdb'}"
                    self.run_cbq_query()
                    created_indexes.append(index_name)

        for bucket in self.buckets:
            tasks=[]
            for index_name in created_indexes:
                try:
                    tasks.append(self.async_monitor_index(bucket = bucket.name, index_name = index_name))
                    for task in tasks:
                        task.result()
                except Exception, ex:
                    self.log.info(ex)

        for bucket in self.buckets:
            try:
             for index_name in created_indexes:
                self.query = "explain SELECT job_title, array_min(array_agg(test_rate)) as rates" + \
                             " FROM %s WHERE join_mo=7 GROUP BY job_title" % (bucket.name)

                if self.covering_index:
                    self.test_explain_covering_index(index_name)
            finally:
                for index_name in set(created_indexes):
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, index_name,self.index_type)
                    self.run_cbq_query()


    def test_array_put_covering_index(self):
         for bucket in self.buckets:
            created_indexes = []
            for ind in xrange(self.num_indexes):
                    index_name = "coveringindexwitharrayput%s" % ind
                    self.query = "CREATE INDEX %s ON %s(join_mo,job_title,name)  USING %s" % (index_name, bucket.name,self.index_type)
                    # if self.gsi_type:
                    #     self.query += " WITH {'index_type': 'memdb'}"
                    self.run_cbq_query()
                    created_indexes.append(index_name)

         for bucket in self.buckets:
            tasks=[]
            for index_name in created_indexes:
                try:
                    tasks.append(self.async_monitor_index(bucket = bucket.name, index_name = index_name))
                    for task in tasks:
                        task.result()
                except Exception, ex:
                    self.log.info(ex)

         for bucket in self.buckets:
             try:
                 self.query = "explain SELECT job_title, array_put(array_agg(distinct name), 'employee-1') as emp_job" + \
                              " FROM %s WHERE join_mo=7 GROUP BY job_title" % (bucket.name)

                 if self.covering_index:
                     self.test_explain_covering_index(index_name)

                 self.query = "SELECT job_title, array_put(array_agg(distinct name), 'employee-1') as emp_job" + \
                              " FROM %s WHERE join_mo=7  GROUP BY job_title" % (bucket.name)

                 actual_list = self.run_cbq_query()
                 actual_result = self.sort_nested_list(actual_list['results'])
                 actual_result = sorted(actual_result,
                                        key=lambda doc: (doc['job_title']))

                 tmp_groups = set([doc['job_title'] for doc in self.full_list if doc['join_mo']==7])
                 expected_result = [{"job_title" : group,
                                     "emp_job" : sorted(set([x["name"] for x in self.full_list
                                                             if x["job_title"] == group]))}
                                    for group in tmp_groups]
                 expected_result = sorted(expected_result, key=lambda doc: (doc['job_title']))
                 self._verify_results(actual_result, expected_result)

                 self.query = "SELECT job_title, array_put(array_agg(distinct name), 'employee-47') as emp_job" + \
                              " FROM %s WHERE join_mo=7 GROUP BY job_title" % (bucket.name)

                 actual_list = self.run_cbq_query()
                 actual_result = self.sort_nested_list(actual_list['results'])
                 actual_result = sorted(actual_result,
                                        key=lambda doc: (doc['job_title']))

                 expected_result = [{"job_title" : group,
                                     "emp_job" : sorted(set([x["name"] for x in self.full_list
                                                             if x["job_title"] == group] + ['employee-47']))}
                                    for group in tmp_groups]
                 expected_result = sorted(expected_result, key=lambda doc: (doc['job_title']))
                 self._verify_results(actual_result, expected_result)
             finally:
                 for index_name in set(created_indexes):
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, index_name,self.index_type)
                    self.run_cbq_query()


    def test_array_replace_covering_index(self):
        for bucket in self.buckets:
            created_indexes = []
            for ind in xrange(self.num_indexes):
                    index_name = "coveringindexwitharrayreplace%s" % ind
                    self.query = "CREATE INDEX %s ON %s(join_mo,job_title,name)  USING %s" % (index_name, bucket.name,self.index_type)
                    # if self.gsi_type:
                    #     self.query += " WITH {'index_type': 'memdb'}"
                    self.run_cbq_query()
                    created_indexes.append(index_name)

        for bucket in self.buckets:
            tasks=[]
            for index_name in created_indexes:
                try:
                    tasks.append(self.async_monitor_index(bucket = bucket.name, index_name = index_name))
                    for task in tasks:
                        task.result()
                except Exception, ex:
                    self.log.info(ex)

        for bucket in self.buckets:
            try:
                self.query = "explain SELECT job_title, array_replace(array_agg(name), 'employee-1', 'employee-47') as emp_job" + \
                             " FROM %s WHERE join_mo=7 GROUP BY job_title" % (bucket.name)

                if self.covering_index:
                    self.test_explain_covering_index(index_name)
            finally:
                for index_name in set(created_indexes):
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, index_name,self.index_type)
                    self.run_cbq_query()

    def test_array_sort_covering_index(self):
        for bucket in self.buckets:
            created_indexes = []
            for ind in xrange(self.num_indexes):
                    index_name = "coveringindexwitharraysort%s" % ind
                    self.query = "CREATE INDEX %s ON %s(join_mo,job_title,test_rate)  USING %s" % (index_name, bucket.name,self.index_type)
                    # if self.gsi_type:
                    #     self.query += " WITH {'index_type': 'memdb'}"
                    self.run_cbq_query()
                    created_indexes.append(index_name)

        for bucket in self.buckets:
            tasks=[]
            for index_name in created_indexes:
                try:
                    tasks.append(self.async_monitor_index(bucket = bucket.name, index_name = index_name))
                    for task in tasks:
                        task.result()
                except Exception, ex:
                    self.log.info(ex)
        for bucket in self.buckets:
            try:
                self.query = "explain SELECT job_title, array_sort(array_agg(distinct test_rate)) as emp_job" +\
                " FROM %s WHERE join_mo=7  GROUP BY job_title" % (bucket.name)

                if self.covering_index:
                    self.test_explain_covering_index(index_name)
            finally:
                for index_name in set(created_indexes):
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, index_name,self.index_type)
                    self.run_cbq_query()


    def test_poly_length_covering_index(self):
        for bucket in self.buckets:
            created_indexes = []
            for ind in xrange(self.num_indexes):
                    index_name = "coveringindexwithpolylength%s" % ind
                    self.query = "CREATE INDEX %s ON %s(join_mo,tasks_points,VMs,skills)  USING %s" % (index_name, bucket.name,self.index_type)
                    # if self.gsi_type:
                    #     self.query += " WITH {'index_type': 'memdb'}"
                    self.run_cbq_query()
                    created_indexes.append(index_name)

        for bucket in self.buckets:
            tasks=[]
            for index_name in created_indexes:
                try:
                    tasks.append(self.async_monitor_index(bucket = bucket.name, index_name = index_name))
                    for task in tasks:
                        task.result()
                except Exception, ex:
                    self.log.info(ex)
        for bucket in self.buckets:
            try:
                query_fields = ['tasks_points', 'VMs', 'skills']
                for query_field in query_fields:
                    self.query = "explain Select length(%s) as custom_num from %s WHERE join_mo=7" % (query_field, bucket.name)
                    if self.covering_index:
                        self.test_explain_covering_index(index_name)
            finally:
                for index_name in set(created_indexes):
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, index_name,self.index_type)
                    self.run_cbq_query()

    def test_maximumlimit_covering_index(self):
        for bucket in self.buckets:
            created_indexes = []
            for ind in xrange(self.num_indexes):
                    index_name = "coveringindexwithmaximumlimit%s" % ind
                    self.query = "CREATE INDEX %s ON %s(join_mo,name,mutated,skills,join_day,email,test_rate,join_yr,_id,VMs[0].RAM,VMs[1].os,job_title)  USING %s" % (index_name, bucket.name,self.index_type)
                    # if self.gsi_type:
                    #     self.query += " WITH {'index_type': 'memdb'}"
                    self.run_cbq_query()
                    created_indexes.append(index_name)

        for bucket in self.buckets:
            tasks=[]
            for index_name in created_indexes:
                try:
                    tasks.append(self.async_monitor_index(bucket = bucket.name, index_name = index_name))
                    for task in tasks:
                        task.result()
                except Exception, ex:
                    self.log.info(ex)
        for bucket in self.buckets:
            try:
                query_fields = ['name', 'mutated', 'skills','join_day','join_mo','email','test_rate','join_yr','_id','VMs[0].RAM','VMs[1].os','job_title']
                for query_field in query_fields:
                    self.query = "explain Select length(%s) as custom_num from %s WHERE join_mo=7" % (query_field, bucket.name)
                    if self.covering_index:
                        self.test_explain_covering_index(index_name)


                    self.query = "Select length(%s) as custom_num from %s WHERE join_mo=7" % (query_field, bucket.name)
                    actual_result = self.run_cbq_query()
                    expected_result = [{"custom_num" : None} for doc in self.full_list if doc['join_mo']==7]
                    self._verify_results(actual_result['results'], expected_result)

                    self.query = "Select poly_length(%s) as custom_num from %s WHERE join_mo=7 " % (query_field, bucket.name)
                    actual_result = self.run_cbq_query()
                    expected_result = [{"custom_num" : len(doc[query_field])}
                                       for doc in self.full_list
                                       if doc['join_mo']==7]
                    expected_result = sorted(expected_result, key=lambda doc: (doc['custom_num']))
                    self._verify_results(actual_result['results'], expected_result)
            finally:
                for index_name in set(created_indexes):
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, index_name,self.index_type)
                    self.run_cbq_query()



    def test_explain_index_join(self):
        for bucket in self.buckets:
            created_indexes = []
            try:
                for ind in xrange(self.num_indexes):
                    index_name = "join_index%s" % ind
                    self.query = "CREATE INDEX %s ON %s(name, project)  USING %s" % (index_name, bucket.name,self.index_type)
                    # if self.gsi_type:
                    #     self.query += " WITH {'index_type': 'memdb'}"
                    self.run_cbq_query()
                    self._wait_for_index_online(bucket, index_name)
                    created_indexes.append(index_name)
                    self.query = "EXPLAIN SELECT employee.name, new_task.project FROM %s as employee JOIN %s as new_task ON KEYS ['key1']" % (bucket.name, bucket.name)
                    res = self.run_cbq_query()
		    plan = ExplainPlanHelper(res)
                    self.assertTrue(plan["~children"][0]["index"] == "#primary",
                                    "Index should be %s, but is: %s" % (index_name, plan))
            finally:
                for index_name in created_indexes:
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, index_name,self.index_type)
                    self.run_cbq_query()

    def test_explain_index_subquery(self):
        for bucket in self.buckets:
            created_indexes = []
            try:
                for ind in xrange(self.num_indexes):
                    index_name = "join_index%s" % ind
                    self.query = "CREATE INDEX %s ON %s(join_day, name)  USING %s" % (index_name, bucket.name,self.index_type)
                    # if self.gsi_type:
                    #     self.query += " WITH {'index_type': 'memdb'}"
                    self.run_cbq_query()
                    self._wait_for_index_online(bucket, index_name)
                    created_indexes.append(index_name)
                    self.query = "EXPLAIN select task_name, (select sum(test_rate) cn from %s as d use keys ['query-1'] where join_day>2 and name =='abc') as names from %s" % (bucket.name, bucket.name)
                    res = self.run_cbq_query()
		    plan = ExplainPlanHelper(res)
                    self.assertTrue(plan["~children"][0]["index"] == "#primary",
                                    "Index should be %s, but is: %s" % (index_name, res["results"]))
            finally:
                for index_name in created_indexes:
                    self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, index_name,self.index_type)
                    self.run_cbq_query()

    def test_explain_childs_list_objects(self):
        for bucket in self.buckets:
            index_name = "my_index_child2"
            try:
                self.query = "CREATE INDEX %s ON %s(distinct array vm.RAM for vm in VMs END)  USING %s" % (index_name, bucket.name,self.index_type)
                # if self.gsi_type:
                #     self.query += " WITH {'index_type': 'memdb'}"
                self.run_cbq_query()
                self._wait_for_index_online(bucket, index_name)
                self.query = 'EXPLAIN SELECT VMs[0].RAM FROM %s ' % (bucket.name) + \
                        'WHERE ANY vm IN VMs SATISFIES vm.RAM > 5 end'
                res = self.run_cbq_query()
		plan = ExplainPlanHelper(res)
                self.assertTrue(plan["~children"][0]["scans"][0]['index']  == index_name,
                                "Index should be %s, but is: %s" % (index_name, plan))
            finally:
                self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, index_name,self.index_type)
                try:
                    self.run_cbq_query()
                except:
                    pass

    def test_explain_childs_objects(self):
        for bucket in self.buckets:
            index_name = "my_index_obj"
            try:
                self.query = "CREATE INDEX %s ON %s(tasks_points.task1, join_mo)  USING %s" % (index_name, bucket.name,self.index_type)
                # if self.gsi_type:
                #     self.query += " WITH {'index_type': 'memdb'}"
                self.run_cbq_query()
                self._wait_for_index_online(bucket, index_name)
                self.query = 'EXPLAIN SELECT tasks_points.task1 AS task from %s ' % (bucket.name) + \
                             'WHERE task_points.task1 > 0 and join_mo>7 '
                res = self.run_cbq_query()
		plan = ExplainPlanHelper(res)
                self.assertTrue(plan["~children"][0]["index"] == index_name,
                                "Index should be %s, but is: %s" % (index_name, res["results"]))
            finally:
                self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, index_name,self.index_type)
                try:
                    self.run_cbq_query()
                except:
                    pass

    # def test_explain_childs_objects_element(self):
    #     for bucket in self.buckets:
    #         index_name = "my_index_obj_el"
    #         try:
    #             self.query = "CREATE INDEX %s ON %s(tasks_points.task1, join_mo)  USING %s" % (index_name, bucket.name,self.index_type)
    #             if self.gsi_type:
    #                 self.query += " WITH {'index_type': 'memdb'}"
    #             self.run_cbq_query()
    #             self._wait_for_index_online(bucket, index_name)
    #             self.query = 'EXPLAIN SELECT tasks_points.task1 AS task from %s ' % (bucket.name) + \
    #                          'WHERE task_points.task1 > 0 and join_mo>7'
    #             res = self.run_cbq_query()
    #		  plan = ExplainPlanHelper(res)
    #             self.assertTrue(plan["~children"][0]["index"] == index_name,
    #                             "Index should be %s, but is: %s" % (index_name, plan))
    #         finally:
    #             self.query = "DROP INDEX %s.%s USING %s" % (bucket.name, index_name,self.index_type)
    #             try:
    #                 self.run_cbq_query()
    #             except:
    #                 pass

    def _is_index_in_list(self, bucket, index_name):
        query = "SELECT * FROM system:indexes"
        res = self.run_cbq_query(query)
        for item in res['results']:
            if 'keyspace_id' not in item['indexes']:
                self.log.error(item)
                continue
            if item['indexes']['keyspace_id'] == bucket.name and item['indexes']['name'] == index_name:
                return True
        return False
