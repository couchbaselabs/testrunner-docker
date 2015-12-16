import logger
import unittest

from membase.helper.rebalance_helper import RebalanceHelper
from couchbase_helper.cluster import Cluster
from basetestcase import BaseTestCase
from remote.remote_util import RemoteMachineShellConnection

from membase.helper.subdoc_helper import SubdocHelper
from random import randint

class SubdocSanityTests(unittest.TestCase):
    def setUp(self):
        self.log = logger.Logger.get_logger()
        self.helper = SubdocHelper(self, "default")
        self.helper.setup_cluster()
        self.cluster = Cluster()
        self.servers = self.helper.servers

    def tearDown(self):
        self.helper.cleanup_cluster()

    def test_simple_dataset_get(self):
        num_docs = self.helper.input.param("num-docs")
        self.log.info("description : Issue simple get sub doc single path "
                      "dataset with {0} docs".format(num_docs))

        data_set = SimpleDataSet(self.helper, num_docs)
        inserted_keys = data_set.load()

        data_set.get_all_docs(inserted_keys, path = 'isDict')
        data_set.get_all_docs(inserted_keys, path='geometry.coordinates[0]')
        data_set.get_all_docs(inserted_keys, path='dict_value.name')
        data_set.get_all_docs(inserted_keys, path='array[0]')
        data_set.get_all_docs(inserted_keys, path='array[-1]')

        ''' This should go into ErrorTesting '''
        #self.assertFalse(data_set.get_all_docs(inserted_keys, path='array[-5]'))
        #self.assertFalse(data_set.get_all_docs(inserted_keys, path='  '))

    def test_simple_dataset_upsert(self):
        num_docs = self.helper.input.param("num-docs")
        self.log.info("description : Issue simple upsert sub doc single path "
                      "dataset with {0} docs".format(num_docs))

        data_set = SimpleDataSet(self.helper, num_docs)
        inserted_keys = data_set.load()

        ''' Randomly generate 1000 long string to replace existing path strings '''
        replace_string = self.generate_string(1000)

        data_set.upsert_all_docs(inserted_keys, replace_string, path='isDict')
        data_set.upsert_all_docs(inserted_keys, replace_string, path='geometry.coordinates[0]')
        data_set.upsert_all_docs(inserted_keys, replace_string, path='dict_value.name')
        data_set.upsert_all_docs(inserted_keys, "999", path='height')
        data_set.upsert_all_docs(inserted_keys, replace_string, path='array[-1]')

    def test_simple_dataset_remove(self):
        num_docs = self.helper.input.param("num-docs")
        self.log.info("description : Issue simple remove sub doc single path "
                      "dataset with {0} docs".format(num_docs))

        data_set = SimpleDataSet(self.helper, num_docs)
        inserted_keys = data_set.load()

        data_set.remove_all_docs(inserted_keys, path='isDict')
        data_set.remove_all_docs(inserted_keys, path='geometry.coordinates[0]')
        data_set.remove_all_docs(inserted_keys, path='dict_value.name')
        data_set.remove_all_docs(inserted_keys, path='array[0]')
        data_set.remove_all_docs(inserted_keys, path='array[-1]')

    def test_simple_dataset_exists(self):
        num_docs = self.helper.input.param("num-docs")
        self.log.info("description : Issue simple exists sub doc single path "
                      "dataset with {0} docs".format(num_docs))

        data_set = SimpleDataSet(self.helper, num_docs)
        inserted_keys = data_set.load()

        ''' add test code to accept Bool values and not error out '''
        data_set.exists_all_docs(inserted_keys, path='isDict')
        data_set.exists_all_docs(inserted_keys, path='geometry.coordinates[0]')
        data_set.exists_all_docs(inserted_keys, path='dict_value.name')
        data_set.exists_all_docs(inserted_keys, path='array[0]')
        data_set.exists_all_docs(inserted_keys, path='array[-1]')

    def test_simple_dataset_replace(self):
        num_docs = self.helper.input.param("num-docs")
        self.log.info("description : Issue simple replace sub doc single path "
                      "dataset with {0} docs".format(num_docs))

        data_set = SimpleDataSet(self.helper, num_docs)
        inserted_keys = data_set.load()

        ''' Randomly generate 1000 long string to replace existing path strings '''
        replace_string = self.generate_string(10)

        data_set.replace_all_docs(inserted_keys, replace_string, path='isDict')
        data_set.replace_all_docs(inserted_keys, replace_string, path='geometry.coordinates[0]')
        data_set.replace_all_docs(inserted_keys, replace_string, path='dict_value.name')
        data_set.replace_all_docs(inserted_keys, "999", path='height')
        data_set.replace_all_docs(inserted_keys, replace_string, path='array[-1]')

    def test_simple_dataset_append(self):
        pass

    def test_simple_dataset_prepend(self):
        pass

    def test_simple_dataset_counter(self):
        pass

    def test_simple_dataset_array_add_unqiue(self):
        pass

    def generate_string(self, range_val=100):
        long_string = ''.join(chr(97 + randint(0, 25)) for i in range(range_val))
        return '"' + long_string + '"'

class SimpleDataSet:
    def __init__(self, helper, num_docs):
        self.helper = helper
        self.num_docs = num_docs
        self.name = "simple_dataset"

    def load(self):
        inserted_keys = self.helper.insert_docs(self.num_docs, self.name)
        return inserted_keys

    def get_all_docs(self, inserted_keys, path):
            for in_key in inserted_keys:
                num_tries = 1
                try:
                    opaque, cas, data = self.helper.client.get_in(in_key, path)
                except Exception as e:
                    self.helper.testcase.fail(
                        "Unable to get key {0} for path {1} after {2} tries"
                        .format(in_key, path, num_tries))

    def upsert_all_docs(self, inserted_keys, long_string, path):
        for in_key in inserted_keys:
            num_tries = 1
            try:
                opaque, cas, data = self.helper.client.upsert_in(in_key, path ,long_string)
            except Exception as e:
                print '[ERROR] {}'.format(e)
                self.helper.testcase.fail(
                    "Unable to upsert key {0} for path {1} after {2} tries"
                    .format(in_key, path, num_tries))

    def remove_all_docs(self, inserted_keys, path):
        for in_key in inserted_keys:
            num_tries = 1
            try:
                opaque, cas, data = self.helper.client.remove_in(in_key, path)
            except Exception as e:
                print '[ERROR] {}'.format(e)
                self.helper.testcase.fail(
                    "Unable to remove value for key {0} for path {1} after {2} tries"
                    .format(in_key, path, num_tries))

    def exists_all_docs(self, inserted_keys, path):
        for in_key in inserted_keys:
            num_tries = 1
            try:
                opaque, cas, data = self.helper.client.exists_in(in_key, path)
            except Exception as e:
                print '[ERROR] {}'.format(e)
                self.helper.testcase.fail(
                    "Unable to validate value for key {0} for path {1} after {2} tries"
                    .format(in_key, path, num_tries))

    def replace_all_docs(self, inserted_keys, long_string, path):
        for in_key in inserted_keys:
            num_tries = 1
            try:
                opaque, cas, data = self.helper.client.replace_in(in_key, path ,long_string)
            except Exception as e:
                print '[ERROR] {}'.format(e)
                self.helper.testcase.fail(
                    "Unable to replace for key {0} for path {1} after {2} tries"
                    .format(in_key, path, num_tries))

    def append_all_paths(self):
        pass

    def prepend_all_paths(self):
        pass

    def counter_all_paths(self):
        pass

    def multi_mutation_all_paths(self):
        pass

class DeeplyNestedDataSet:
    def __init__(self, helper, num_docs):
        self.helper = helper
        self.num_docs = num_docs
        self.name = "deeplynested_dataset"

    def load(self):
        inserted_keys = self.helper.insert_docs(self.num_docs, self.name)
        return inserted_keys

    def get_all_docs(self):
            pass

    def upsert_all_docs(self):
        pass



