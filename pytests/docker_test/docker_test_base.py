import copy
import json
import logging
import random
import string
import subprocess
import traceback
import unittest
import time

import logger
import testconstants
from TestInput import TestInputSingleton, TestInputServer
from basetestcase import BaseTestCase
from couchbase_helper.cluster import Cluster
from membase.api.rest_client import RestConnection, Bucket
from couchbase_helper.documentgenerator import DocumentGenerator
from remote.remote_util import  RemoteMachineShellConnection

class DockerTestBase(BaseTestCase):
    suite_setup_done = False
    def setUp(self):
        start = time.time()
        self.log = logger.Logger.get_logger()
        self.input = TestInputSingleton.input
        self.servers = self.input.servers
        self.nodes_init = self.input.param("nodes_init", 2)
        self.master = self.servers[0]
        #self.master.ip = "localhost"
        #self.populate_yml_file = "populated.yml"
        self.log.info("Bringing up the images now")
        #docker_compose_cmd = "docker-compose -f %s up -d" % \
        #                     self.populate_yml_file
        base_images = ["couchdata:15m", "couchdata:15m",
                       "couchdata6.6:baseline", "couchdata6.6:baseline"]
        docker_run_cmd = "docker run -d --name couchbase --net=host {0}"
        for i in range(self.servers.__len__()):
            server = self.servers[i]
            base_image_name = base_images[i]
            _docker_run_cmd = docker_run_cmd.format(base_image_name)
            remote_connection = RemoteMachineShellConnection(server)
            remote_connection.execute_command(_docker_run_cmd)
            remote_connection.disconnect()
        #subprocess.run(docker_compose_cmd, capture_output=True,
        #              shell=True)
        self.rest = RestConnection(self.master)
        self.cluster = Cluster()
        default_params = self._create_bucket_params(
            server=self.master, size=100)
        self.buckets = []
        self.buckets.append(Bucket(name="default",
                                   authType="sasl",
                                   saslPassword="",
                                   num_replicas=default_params[
                                       'replicas'],
                                   bucket_size=100,
                                   eviction_policy=default_params[
                                       'eviction_policy'],
                                   lww=default_params['lww'],
                                   type=default_params[
                                       'bucket_type']))
        end = time.time()
        self.log.info("Time for test setup: {}".format(end - start))
        self.sleep(20)

    def tearDown(self):
        self.log.info("Tearing down the images now.")
        for i in range(self.servers.__len__()):
            server = self.servers[i]
            remote_connection = RemoteMachineShellConnection(server)
            docker_stop_cmd = "docker stop couchbase"
            remote_connection.execute_command(docker_stop_cmd)
            docker_rm_cmd = "docker rm $(docker  ps -a -l -q -f " \
                            "name=couchbase)"
            remote_connection.execute_command(docker_rm_cmd)
            remote_connection.disconnect()
        # docker_compose_cmd = "docker-compose -f %s down" % \
        #                      self.populate_yml_file
        # subprocess.run(docker_compose_cmd, capture_output=True,
        #                shell=True)

    def suite_setUp(self):
        super(DockerTestBase, self).suite_setUp()
        DockerTestBase.suite_setup_done = True

    def suite_tearDown(self):
        super(DockerTestBase, self).suite_tearDown()

    def create_yml_file(self, file_name="baseline",
                        image=None, servers=None,
                        with_master=True, different_ports=None):
        if different_ports is None:
            different_ports = []
        if image is None:
            image = ["couchdata:baseline"]
        if servers is None:
            servers = []
        with open(file_name, 'w+') as yml_file:
            yml_file.writelines("version: \"2\"\n")
            yml_file.writelines("services:\n")
            if with_master:
                with open("yml/master.yml", 'r') as master_yml:
                    master_yml_template = master_yml.read()
                    master = servers[0]
                    master_yml_template = master_yml_template.replace(
                        "<image>", image[0])
                    master_yml_template = master_yml_template.replace(
                        "<ip>", master.ip)
                    yml_file.write(master_yml_template)
            with open("yml/node.yml", "r") as node_yml:
                node_template = node_yml.read()
                different_ports_ip = [node.ip for node in different_ports]
                for i in range(1, servers.__len__()):
                    server = servers[i]
                    if server.ip in different_ports_ip:
                        with open("yml/node_with_ports.yml") as \
                                different_ports_yml:
                            temp_node_template = different_ports_yml.read()
                    else:
                        temp_node_template = node_template
                    temp_node_template = temp_node_template.replace(
                        "<number>", i.__str__())
                    temp_node_template = temp_node_template.replace("<image>", image[i])
                    temp_node_template = temp_node_template.replace("<ip>", server.ip)
                    yml_file.write(temp_node_template)
            with open("yml/network.yml", 'r+') as network_yml:
                network_yml_template = network_yml.read()
                yml_file.writelines("networks:\n")
                yml_file.write(network_yml_template)

    def _create_bucket_params(self, server, replicas=1, size=0, port=11211, password=None,
                              bucket_type='membase', enable_replica_index=1, eviction_policy='valueOnly',
                              bucket_priority=None, flush_enabled=1, lww=False, maxttl=None,
                              compression_mode='passive'):
        """Create a set of bucket_parameters to be sent to all of the bucket_creation methods
        Parameters:
            server - The server to create the bucket on. (TestInputServer)
            port - The port to create this bucket on. (String)
            password - The password for this bucket. (String)
            size - The size of the bucket to be created. (int)
            enable_replica_index - can be 0 or 1, 1 enables indexing of replica bucket data (int)
            replicas - The number of replicas for this bucket. (int)
            eviction_policy - The eviction policy for the bucket (String). Can be
                ephemeral bucket: noEviction or nruEviction
                non-ephemeral bucket: valueOnly or fullEviction.
            bucket_priority - The priority of the bucket:either none, low, or high. (String)
            bucket_type - The type of bucket. (String)
            flushEnabled - Enable or Disable the flush functionality of the bucket. (int)
            lww = determine the conflict resolution type of the bucket. (Boolean)

        Returns:
            bucket_params - A dictionary containing the parameters needed to create a bucket."""

        bucket_params = dict()
        bucket_params['server'] = server or self.master
        bucket_params['replicas'] = replicas
        bucket_params['size'] = size
        bucket_params['port'] = port
        bucket_params['password'] = password
        bucket_params['bucket_type'] = bucket_type
        bucket_params['enable_replica_index'] = enable_replica_index
        bucket_params['eviction_policy'] = eviction_policy
        bucket_params['bucket_priority'] = bucket_priority
        bucket_params['flush_enabled'] = flush_enabled
        bucket_params['lww'] = lww
        bucket_params['maxTTL'] = maxttl
        bucket_params['compressionMode'] = compression_mode
        return bucket_params


    def _async_load_bucket(self, bucket, server, kv_gen, op_type, exp, kv_store=1, flag=0, only_store_hash=True,
                           batch_size=1000, pause_secs=1, timeout_secs=30, scope=None, collection=None):
        gen = copy.deepcopy(kv_gen)
        task = self.cluster.async_load_gen_docs(server, bucket.name, gen,
                                                bucket.kvs[kv_store], op_type,
                                                exp, flag, only_store_hash,
                                                batch_size, pause_secs, timeout_secs,
                                                compression=True)
        return task

    def _load_bucket(self, bucket, server, kv_gen, op_type, exp, kv_store=1, flag=0, only_store_hash=True,
                     batch_size=1000, pause_secs=1, timeout_secs=30, scope=None, collection=None):
        task = self._async_load_bucket(bucket, server, kv_gen, op_type, exp, kv_store, flag, only_store_hash,
                                       batch_size, pause_secs, timeout_secs, scope=scope, collection=collection)
        task.result()

    def _load_doc_data_all_buckets(self, data_op="create", batch_size=1000, gen_load=None):
        # initialize the template for document generator
        age = range(5)
        first = ['james', 'sharon']
        template = '{{ "mutated" : 0, "age": {0}, "first_name": "{1}" }}'
        if gen_load is None:
            gen_load = DocumentGenerator('test_docs', template, age, first, start=0, end=self.num_items)

        self.log.info("%s %s documents..." % (data_op, self.num_items))
        self._load_all_buckets(self.master, gen_load, data_op, 0, batch_size=batch_size)
        return gen_load