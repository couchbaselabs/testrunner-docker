import time, os, json

from threading import Thread
import threading
from basetestcase import BaseTestCase
from rebalance.rebalance_base import RebalanceBaseTest
from membase.api.exception import RebalanceFailedException
from membase.api.rest_client import RestConnection, RestHelper
from couchbase_helper.documentgenerator import BlobGenerator
from membase.helper.rebalance_helper import RebalanceHelper
from remote.remote_util import RemoteMachineShellConnection
from membase.helper.cluster_helper import ClusterOperationHelper


class AutoRetryFailedRebalance(RebalanceBaseTest):

    def setUp(self):
        super(AutoRetryFailedRebalance, self).setUp()
        self.sleep_time = self.input.param("sleep_time", 15)
        self.enabled = self.input.param("enabled", True)
        self.afterTimePeriod = self.input.param("afterTimePeriod", 300)
        self.maxAttempts = self.input.param("maxAttempts", 1)
        self.log.info("Changing the retry rebalance settings ....")
        self.change_retry_rebalance_settings(enabled=self.enabled, afterTimePeriod=self.afterTimePeriod,
                                             maxAttempts=self.maxAttempts)

    def tearDown(self):
        self.reset_retry_rebalance_settings()
        super(AutoRetryFailedRebalance, self).tearDown()

    def test_auto_retry_of_failed_rebalance_where_failure_happens_before_rebalance(self):
        before_rebalance_failure = self.input.param("before_rebalance_failure", "stop_server")
        # induce the failure before the rebalance starts
        if before_rebalance_failure == "stop_server":
            self.stop_server(self.servers[1])
        elif before_rebalance_failure == "enable_firewall":
            self.start_firewall_on_node(self.servers[1])
        self.sleep(self.sleep_time)
        try:
            rebalance = self.cluster.async_rebalance(self.servers[:self.nodes_init], [], self.servers[1:])
            rebalance.result()
        except Exception:
            # recover from the failure before the retry of rebalance
            if before_rebalance_failure == "stop_server":
                self.start_server(self.servers[1])
            elif before_rebalance_failure == "enable_firewall":
                self.stop_firewall_on_node(self.servers[1])
            rest = RestConnection(self.servers[0])
            result = json.loads(rest.get_pending_rebalance_info())
            retry_after_secs = result["retry_after_secs"]
            attempts_remaining = result["attempts_remaining"]
            retry_rebalance = result["retry_rebalance"]
            while retry_rebalance == "pending" and attempts_remaining:
                # wait for the afterTimePeriod for the failed rebalance to restart
                self.sleep(retry_after_secs, message="Waiting for the afterTimePeriod to complete")
                result = rest.monitorRebalance()
                msg = "successfully rebalanced cluster {0}"
                self.log.info(msg.format(result))
                result = json.loads(rest.get_pending_rebalance_info())
                self.log.info(msg.format(result))
                retry_rebalance = result["retry_rebalance"]
                if retry_rebalance == "not_pending":
                    break
                attempts_remaining = result["attempts_remaining"]
                retry_rebalance = result["retry_rebalance"]
                retry_after_secs = result["retry_after_secs"]
        else:
            self.fail("Rebalance did not fail as expected. Hence could not validate auto-retry feature..")
        finally:
            self.stop_firewall_on_node(self.servers[1])

    def test_auto_retry_of_failed_rebalance_where_failure_happens_during_rebalance(self):
        during_rebalance_failure = self.input.param("during_rebalance_failure", "stop_server")
        try:
            rebalance = self.cluster.async_rebalance(self.servers[:self.nodes_init], [], self.servers[1:])
            self.sleep(self.sleep_time)
            # induce the failure during the rebalance
            if during_rebalance_failure == "stop_server":
                self.stop_server(self.servers[1])
            elif during_rebalance_failure == "kill_memcached":
                self.kill_server_memcached(self.servers[1])
            elif during_rebalance_failure == "enable_firewall":
                self.start_firewall_on_node(self.servers[1])
            elif during_rebalance_failure == "reboot_server":
                shell = RemoteMachineShellConnection(self.servers[1])
                shell.reboot_node()
            rebalance.result()
        except Exception:
            # recover from the failure before the retry of rebalance
            if during_rebalance_failure == "stop_server":
                self.start_server(self.servers[1])
            elif during_rebalance_failure == "enable_firewall":
                self.stop_firewall_on_node(self.servers[1])
            elif during_rebalance_failure == "reboot_server":
                # wait till node is ready after warmup
                ClusterOperationHelper.wait_for_ns_servers_or_assert([self.servers[1]], self, wait_if_warmup=True)
            rest = RestConnection(self.servers[0])
            result = json.loads(rest.get_pending_rebalance_info())
            self.sleep(self.sleep_time)
            retry_after_secs = result["retry_after_secs"]
            attempts_remaining = result["attempts_remaining"]
            retry_rebalance = result["retry_rebalance"]
            while retry_rebalance == "pending" and attempts_remaining:
                # wait for the afterTimePeriod for the failed rebalance to restart
                self.sleep(retry_after_secs, message="Waiting for the afterTimePeriod to complete")
                result = rest.monitorRebalance()
                msg = "successfully rebalanced cluster {0}"
                self.log.info(msg.format(result))
                result = json.loads(rest.get_pending_rebalance_info())
                self.log.info(msg.format(result))
                retry_rebalance = result["retry_rebalance"]
                if retry_rebalance == "not_pending":
                    break
                attempts_remaining = result["attempts_remaining"]
                retry_rebalance = result["retry_rebalance"]
                retry_after_secs = result["retry_after_secs"]
        else:
            self.fail("Rebalance did not fail as expected. Hence could not validate auto-retry feature..")
        finally:
            self.stop_firewall_on_node(self.servers[1])
