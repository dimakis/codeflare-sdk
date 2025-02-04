# Copyright 2022 IBM, Red Hat
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from pathlib import Path
import sys
import filecmp
import os
import re

parent = Path(__file__).resolve().parents[1]
sys.path.append(str(parent) + "/src")

from codeflare_sdk.cluster.awload import AWManager
from codeflare_sdk.cluster.cluster import (
    Cluster,
    ClusterConfiguration,
    list_all_clusters,
    list_all_queued,
    _copy_to_ray,
)
from codeflare_sdk.cluster.auth import (
    TokenAuthentication,
    PasswordUserAuthentication,
    Authentication,
)
from codeflare_sdk.utils.pretty_print import (
    print_no_resources_found,
    print_app_wrappers_status,
    print_cluster_status,
    print_clusters,
)
from codeflare_sdk.cluster.model import (
    AppWrapper,
    RayCluster,
    AppWrapperStatus,
    RayClusterStatus,
    CodeFlareClusterStatus,
)
from codeflare_sdk.job.jobs import (
    JobDefinition,
    Job,
    DDPJobDefinition,
    DDPJob,
    torchx_runner,
)
import openshift
from openshift import OpenShiftPythonException
from openshift.selector import Selector
import ray
from torchx.specs import AppDryRunInfo, AppDef
from torchx.runner import get_runner, Runner
from torchx.schedulers.ray_scheduler import RayJob
from torchx.schedulers.kubernetes_mcad_scheduler import KubernetesMCADJob
import pytest


# For mocking openshift client results
fake_res = openshift.Result("fake")


def arg_side_effect(*args):
    fake_res.high_level_operation = args
    return fake_res


def att_side_effect(self):
    return self.high_level_operation


def att_side_effect_tls(self):
    if "--insecure-skip-tls-verify" in self.high_level_operation[1]:
        return self.high_level_operation
    else:
        raise OpenShiftPythonException(
            "The server uses a certificate signed by unknown authority"
        )


def test_token_auth_creation():
    try:
        token_auth = TokenAuthentication()
        assert token_auth.token == None
        assert token_auth.server == None
        assert token_auth.skip_tls == False

        token_auth = TokenAuthentication("token")
        assert token_auth.token == "token"
        assert token_auth.server == None
        assert token_auth.skip_tls == False

        token_auth = TokenAuthentication("token", "server")
        assert token_auth.token == "token"
        assert token_auth.server == "server"
        assert token_auth.skip_tls == False

        token_auth = TokenAuthentication("token", server="server")
        assert token_auth.token == "token"
        assert token_auth.server == "server"
        assert token_auth.skip_tls == False

        token_auth = TokenAuthentication(token="token", server="server")
        assert token_auth.token == "token"
        assert token_auth.server == "server"
        assert token_auth.skip_tls == False

        token_auth = TokenAuthentication(token="token", server="server", skip_tls=True)
        assert token_auth.token == "token"
        assert token_auth.server == "server"
        assert token_auth.skip_tls == True

    except Exception:
        assert 0 == 1


def test_token_auth_login_logout(mocker):
    mocker.patch("openshift.invoke", side_effect=arg_side_effect)
    mock_res = mocker.patch.object(openshift.Result, "out")
    mock_res.side_effect = lambda: att_side_effect(fake_res)

    token_auth = TokenAuthentication(token="testtoken", server="testserver:6443")
    assert token_auth.login() == (
        "login",
        ["--token=testtoken", "--server=testserver:6443"],
    )
    assert token_auth.logout() == (
        "logout",
        ["--token=testtoken", "--server=testserver:6443"],
    )


def test_token_auth_login_tls(mocker):
    mocker.patch("openshift.invoke", side_effect=arg_side_effect)
    mock_res = mocker.patch.object(openshift.Result, "out")
    mock_res.side_effect = lambda: att_side_effect_tls(fake_res)

    # FIXME - Pytest mocker not allowing caught exception
    # token_auth = TokenAuthentication(token="testtoken", server="testserver")
    # assert token_auth.login() == "Error: certificate auth failure, please set `skip_tls=True` in TokenAuthentication"

    token_auth = TokenAuthentication(
        token="testtoken", server="testserver:6443", skip_tls=True
    )
    assert token_auth.login() == (
        "login",
        ["--token=testtoken", "--server=testserver:6443", "--insecure-skip-tls-verify"],
    )


def test_passwd_auth_creation():
    try:
        passwd_auth = PasswordUserAuthentication()
        assert passwd_auth.username == None
        assert passwd_auth.password == None

        passwd_auth = PasswordUserAuthentication("user")
        assert passwd_auth.username == "user"
        assert passwd_auth.password == None

        passwd_auth = PasswordUserAuthentication("user", "passwd")
        assert passwd_auth.username == "user"
        assert passwd_auth.password == "passwd"

        passwd_auth = PasswordUserAuthentication("user", password="passwd")
        assert passwd_auth.username == "user"
        assert passwd_auth.password == "passwd"

        passwd_auth = PasswordUserAuthentication(username="user", password="passwd")
        assert passwd_auth.username == "user"
        assert passwd_auth.password == "passwd"

    except Exception:
        assert 0 == 1


def test_passwd_auth_login_logout(mocker):
    mocker.patch("openshift.invoke", side_effect=arg_side_effect)
    mocker.patch("openshift.login", side_effect=arg_side_effect)
    mock_res = mocker.patch.object(openshift.Result, "out")
    mock_res.side_effect = lambda: att_side_effect(fake_res)

    token_auth = PasswordUserAuthentication(username="user", password="passwd")
    assert token_auth.login() == ("user", "passwd")
    assert token_auth.logout() == ("logout",)


def test_auth_coverage():
    abstract = Authentication()
    abstract.login()
    abstract.logout()


def test_config_creation():
    config = ClusterConfiguration(
        name="unit-test-cluster",
        namespace="ns",
        min_worker=1,
        max_worker=2,
        min_cpus=3,
        max_cpus=4,
        min_memory=5,
        max_memory=6,
        gpu=7,
        instascale=True,
        machine_types=["cpu.small", "gpu.large"],
    )

    assert config.name == "unit-test-cluster" and config.namespace == "ns"
    assert config.min_worker == 1 and config.max_worker == 2
    assert config.min_cpus == 3 and config.max_cpus == 4
    assert config.min_memory == 5 and config.max_memory == 6
    assert config.gpu == 7
    assert (
        config.image
        == "ghcr.io/foundation-model-stack/base:ray2.1.0-py38-gpu-pytorch1.12.0cu116-20221213-193103"
    )
    assert config.template == f"{parent}/src/codeflare_sdk/templates/new-template.yaml"
    assert config.instascale
    assert config.machine_types == ["cpu.small", "gpu.large"]
    return config


def test_cluster_creation():
    cluster = Cluster(test_config_creation())
    assert cluster.app_wrapper_yaml == "unit-test-cluster.yaml"
    assert cluster.app_wrapper_name == "unit-test-cluster"
    assert filecmp.cmp(
        "unit-test-cluster.yaml", f"{parent}/tests/test-case.yaml", shallow=True
    )
    return cluster


def test_default_cluster_creation(mocker):
    mocker.patch(
        "openshift.get_project_name",
        return_value="opendatahub",
    )
    default_config = ClusterConfiguration(
        name="unit-test-default-cluster",
    )
    cluster = Cluster(default_config)

    assert cluster.app_wrapper_yaml == "unit-test-default-cluster.yaml"
    assert cluster.app_wrapper_name == "unit-test-default-cluster"
    assert cluster.config.namespace == "opendatahub"

    return cluster


def arg_check_apply_effect(*args):
    assert args[0] == "apply"
    assert args[1] == ["-f", "unit-test-cluster.yaml"]


def arg_check_del_effect(*args):
    assert args[0] == "delete"
    assert args[1] == ["AppWrapper", "unit-test-cluster"]


def test_cluster_up_down(mocker):
    mocker.patch(
        "codeflare_sdk.cluster.auth.TokenAuthentication.login", return_value="ignore"
    )
    mocker.patch(
        "codeflare_sdk.cluster.auth.TokenAuthentication.logout", return_value="ignore"
    )
    mocker.patch("openshift.invoke", side_effect=arg_check_apply_effect)
    cluster = test_cluster_creation()
    cluster.up()
    mocker.patch("openshift.invoke", side_effect=arg_check_del_effect)
    cluster.down()


def out_route(self):
    return "ray-dashboard-raycluster-autoscaler-ns.apps.cluster.awsroute.org ray-dashboard-unit-test-cluster-ns.apps.cluster.awsroute.org"


def test_cluster_uris(mocker):
    mocker.patch("openshift.invoke", return_value=fake_res)
    mock_res = mocker.patch.object(openshift.Result, "out")
    mock_res.side_effect = lambda: out_route(fake_res)

    cluster = test_cluster_creation()
    assert cluster.cluster_uri() == "ray://unit-test-cluster-head-svc.ns.svc:10001"
    assert (
        cluster.cluster_dashboard_uri()
        == "http://ray-dashboard-unit-test-cluster-ns.apps.cluster.awsroute.org"
    )
    cluster.config.name = "fake"
    assert (
        cluster.cluster_dashboard_uri()
        == "Dashboard route not available yet, have you run cluster.up()?"
    )


def ray_addr(self, *args):
    return self._address


def test_ray_job_wrapping(mocker):
    mocker.patch("openshift.invoke", return_value=fake_res)
    mock_res = mocker.patch.object(openshift.Result, "out")
    mock_res.side_effect = lambda: out_route(fake_res)
    cluster = test_cluster_creation()

    mocker.patch(
        "ray.job_submission.JobSubmissionClient._check_connection_and_version_with_url",
        return_value="None",
    )
    mock_res = mocker.patch.object(
        ray.job_submission.JobSubmissionClient, "list_jobs", autospec=True
    )
    mock_res.side_effect = ray_addr
    assert cluster.list_jobs() == cluster.cluster_dashboard_uri()

    mock_res = mocker.patch.object(
        ray.job_submission.JobSubmissionClient, "get_job_status", autospec=True
    )
    mock_res.side_effect = ray_addr
    assert cluster.job_status("fake_id") == cluster.cluster_dashboard_uri()

    mock_res = mocker.patch.object(
        ray.job_submission.JobSubmissionClient, "get_job_logs", autospec=True
    )
    mock_res.side_effect = ray_addr
    assert cluster.job_logs("fake_id") == cluster.cluster_dashboard_uri()


def test_print_no_resources(capsys):
    try:
        print_no_resources_found()
    except:
        assert 1 == 0
    captured = capsys.readouterr()
    assert captured.out == (
        "╭──────────────────────────────────────────────────────────────────────────────╮\n"
        "│ No resources found, have you run cluster.up() yet?                           │\n"
        "╰──────────────────────────────────────────────────────────────────────────────╯\n"
    )


def test_print_no_cluster(capsys):
    try:
        print_cluster_status(None)
    except:
        assert 1 == 0
    captured = capsys.readouterr()
    assert captured.out == (
        "╭──────────────────────────────────────────────────────────────────────────────╮\n"
        "│ No resources found, have you run cluster.up() yet?                           │\n"
        "╰──────────────────────────────────────────────────────────────────────────────╯\n"
    )


def test_print_appwrappers(capsys):
    aw1 = AppWrapper(
        name="awtest1",
        status=AppWrapperStatus.PENDING,
        can_run=False,
        job_state="queue-state",
    )
    aw2 = AppWrapper(
        name="awtest2",
        status=AppWrapperStatus.RUNNING,
        can_run=False,
        job_state="queue-state",
    )
    try:
        print_app_wrappers_status([aw1, aw2])
    except:
        assert 1 == 0
    captured = capsys.readouterr()
    assert captured.out == (
        "╭───────────────────────╮\n"
        "│    🚀 Cluster Queue   │\n"
        "│       Status 🚀       │\n"
        "│ +---------+---------+ │\n"
        "│ | Name    | Status  | │\n"
        "│ +=========+=========+ │\n"
        "│ | awtest1 | pending | │\n"
        "│ |         |         | │\n"
        "│ | awtest2 | running | │\n"
        "│ |         |         | │\n"
        "│ +---------+---------+ │\n"
        "╰───────────────────────╯\n"
    )


def test_ray_details(capsys):
    ray1 = RayCluster(
        name="raytest1",
        status=RayClusterStatus.READY,
        min_workers=1,
        max_workers=1,
        worker_mem_min=2,
        worker_mem_max=2,
        worker_cpu=1,
        worker_gpu=0,
        namespace="ns",
        dashboard="fake-uri",
    )
    cf = Cluster(ClusterConfiguration(name="raytest2", namespace="ns"))
    captured = capsys.readouterr()
    ray2 = _copy_to_ray(cf)
    details = cf.details()
    assert details == ray2
    assert ray2.name == "raytest2"
    assert ray1.namespace == ray2.namespace
    assert ray1.min_workers == ray2.min_workers
    assert ray1.max_workers == ray2.max_workers
    assert ray1.worker_mem_min == ray2.worker_mem_min
    assert ray1.worker_mem_max == ray2.worker_mem_max
    assert ray1.worker_cpu == ray2.worker_cpu
    assert ray1.worker_gpu == ray2.worker_gpu
    try:
        print_clusters([ray1, ray2])
        print_cluster_status(ray1)
        print_cluster_status(ray2)
    except:
        assert 0 == 1
    captured = capsys.readouterr()
    assert captured.out == (
        "                  🚀 CodeFlare Cluster Details 🚀                 \n"
        "                                                                  \n"
        " ╭──────────────────────────────────────────────────────────────╮ \n"
        " │   Name                                                       │ \n"
        " │   raytest2                                   Inactive ❌     │ \n"
        " │                                                              │ \n"
        " │   URI: ray://raytest2-head-svc.ns.svc:10001                  │ \n"
        " │                                                              │ \n"
        " │   Dashboard🔗                                                │ \n"
        " │                                                              │ \n"
        " │                      Cluster Resources                       │ \n"
        " │   ╭─ Workers ──╮  ╭───────── Worker specs(each) ─────────╮   │ \n"
        " │   │  Min  Max  │  │  Memory      CPU         GPU         │   │ \n"
        " │   │            │  │                                      │   │ \n"
        " │   │  1    1    │  │  2~2         1           0           │   │ \n"
        " │   │            │  │                                      │   │ \n"
        " │   ╰────────────╯  ╰──────────────────────────────────────╯   │ \n"
        " ╰──────────────────────────────────────────────────────────────╯ \n"
        "                  🚀 CodeFlare Cluster Details 🚀                 \n"
        "                                                                  \n"
        " ╭──────────────────────────────────────────────────────────────╮ \n"
        " │   Name                                                       │ \n"
        " │   raytest1                                   Active ✅       │ \n"
        " │                                                              │ \n"
        " │   URI: ray://raytest1-head-svc.ns.svc:10001                  │ \n"
        " │                                                              │ \n"
        " │   Dashboard🔗                                                │ \n"
        " │                                                              │ \n"
        " │                      Cluster Resources                       │ \n"
        " │   ╭─ Workers ──╮  ╭───────── Worker specs(each) ─────────╮   │ \n"
        " │   │  Min  Max  │  │  Memory      CPU         GPU         │   │ \n"
        " │   │            │  │                                      │   │ \n"
        " │   │  1    1    │  │  2~2         1           0           │   │ \n"
        " │   │            │  │                                      │   │ \n"
        " │   ╰────────────╯  ╰──────────────────────────────────────╯   │ \n"
        " ╰──────────────────────────────────────────────────────────────╯ \n"
        "╭──────────────────────────────────────────────────────────────╮\n"
        "│   Name                                                       │\n"
        "│   raytest2                                   Inactive ❌     │\n"
        "│                                                              │\n"
        "│   URI: ray://raytest2-head-svc.ns.svc:10001                  │\n"
        "│                                                              │\n"
        "│   Dashboard🔗                                                │\n"
        "│                                                              │\n"
        "│                      Cluster Resources                       │\n"
        "│   ╭─ Workers ──╮  ╭───────── Worker specs(each) ─────────╮   │\n"
        "│   │  Min  Max  │  │  Memory      CPU         GPU         │   │\n"
        "│   │            │  │                                      │   │\n"
        "│   │  1    1    │  │  2~2         1           0           │   │\n"
        "│   │            │  │                                      │   │\n"
        "│   ╰────────────╯  ╰──────────────────────────────────────╯   │\n"
        "╰──────────────────────────────────────────────────────────────╯\n"
        "                🚀 CodeFlare Cluster Status 🚀                \n"
        "                                                              \n"
        " ╭──────────────────────────────────────────────────────────╮ \n"
        " │   Name                                                   │ \n"
        " │   raytest1                                   Active ✅   │ \n"
        " │                                                          │ \n"
        " │   URI: ray://raytest1-head-svc.ns.svc:10001              │ \n"
        " │                                                          │ \n"
        " │   Dashboard🔗                                            │ \n"
        " │                                                          │ \n"
        " ╰──────────────────────────────────────────────────────────╯ \n"
        "                 🚀 CodeFlare Cluster Status 🚀                 \n"
        "                                                                \n"
        " ╭────────────────────────────────────────────────────────────╮ \n"
        " │   Name                                                     │ \n"
        " │   raytest2                                   Inactive ❌   │ \n"
        " │                                                            │ \n"
        " │   URI: ray://raytest2-head-svc.ns.svc:10001                │ \n"
        " │                                                            │ \n"
        " │   Dashboard🔗                                              │ \n"
        " │                                                            │ \n"
        " ╰────────────────────────────────────────────────────────────╯ \n"
    )


def act_side_effect_list(self):
    print([self])
    self.out = str(self.high_level_operation)
    return [self]


def get_selector(*args):
    selector = Selector({"operation": "selector", "status": 0, "actions": []})
    return selector


def get_obj_none():
    return []


def get_ray_obj(cls=None):
    api_obj = openshift.apiobject.APIObject(
        {
            "apiVersion": "ray.io/v1alpha1",
            "kind": "RayCluster",
            "metadata": {
                "creationTimestamp": "2023-02-22T16:26:07Z",
                "generation": 1,
                "labels": {
                    "appwrapper.mcad.ibm.com": "quicktest",
                    "controller-tools.k8s.io": "1.0",
                    "resourceName": "quicktest",
                },
                "managedFields": [
                    {
                        "apiVersion": "ray.io/v1alpha1",
                        "fieldsType": "FieldsV1",
                        "fieldsV1": {
                            "f:metadata": {
                                "f:labels": {
                                    ".": {},
                                    "f:appwrapper.mcad.ibm.com": {},
                                    "f:controller-tools.k8s.io": {},
                                    "f:resourceName": {},
                                },
                                "f:ownerReferences": {
                                    ".": {},
                                    'k:{"uid":"6334fc1b-471e-4876-8e7b-0b2277679235"}': {},
                                },
                            },
                            "f:spec": {
                                ".": {},
                                "f:autoscalerOptions": {
                                    ".": {},
                                    "f:idleTimeoutSeconds": {},
                                    "f:imagePullPolicy": {},
                                    "f:resources": {
                                        ".": {},
                                        "f:limits": {
                                            ".": {},
                                            "f:cpu": {},
                                            "f:memory": {},
                                        },
                                        "f:requests": {
                                            ".": {},
                                            "f:cpu": {},
                                            "f:memory": {},
                                        },
                                    },
                                    "f:upscalingMode": {},
                                },
                                "f:enableInTreeAutoscaling": {},
                                "f:headGroupSpec": {
                                    ".": {},
                                    "f:rayStartParams": {
                                        ".": {},
                                        "f:block": {},
                                        "f:dashboard-host": {},
                                        "f:num-gpus": {},
                                    },
                                    "f:serviceType": {},
                                    "f:template": {
                                        ".": {},
                                        "f:spec": {".": {}, "f:containers": {}},
                                    },
                                },
                                "f:rayVersion": {},
                                "f:workerGroupSpecs": {},
                            },
                        },
                        "manager": "mcad-controller",
                        "operation": "Update",
                        "time": "2023-02-22T16:26:07Z",
                    },
                    {
                        "apiVersion": "ray.io/v1alpha1",
                        "fieldsType": "FieldsV1",
                        "fieldsV1": {
                            "f:status": {
                                ".": {},
                                "f:availableWorkerReplicas": {},
                                "f:desiredWorkerReplicas": {},
                                "f:endpoints": {
                                    ".": {},
                                    "f:client": {},
                                    "f:dashboard": {},
                                    "f:gcs": {},
                                },
                                "f:lastUpdateTime": {},
                                "f:maxWorkerReplicas": {},
                                "f:minWorkerReplicas": {},
                                "f:state": {},
                            }
                        },
                        "manager": "manager",
                        "operation": "Update",
                        "subresource": "status",
                        "time": "2023-02-22T16:26:16Z",
                    },
                ],
                "name": "quicktest",
                "namespace": "ns",
                "ownerReferences": [
                    {
                        "apiVersion": "mcad.ibm.com/v1beta1",
                        "blockOwnerDeletion": True,
                        "controller": True,
                        "kind": "AppWrapper",
                        "name": "quicktest",
                        "uid": "6334fc1b-471e-4876-8e7b-0b2277679235",
                    }
                ],
                "resourceVersion": "9482407",
                "uid": "44d45d1f-26c8-43e7-841f-831dbd8c1285",
            },
            "spec": {
                "autoscalerOptions": {
                    "idleTimeoutSeconds": 60,
                    "imagePullPolicy": "Always",
                    "resources": {
                        "limits": {"cpu": "500m", "memory": "512Mi"},
                        "requests": {"cpu": "500m", "memory": "512Mi"},
                    },
                    "upscalingMode": "Default",
                },
                "enableInTreeAutoscaling": False,
                "headGroupSpec": {
                    "rayStartParams": {
                        "block": "true",
                        "dashboard-host": "0.0.0.0",
                        "num-gpus": "0",
                    },
                    "serviceType": "ClusterIP",
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "image": "ghcr.io/foundation-model-stack/base:ray2.1.0-py38-gpu-pytorch1.12.0cu116-20221213-193103",
                                    "imagePullPolicy": "Always",
                                    "lifecycle": {
                                        "preStop": {
                                            "exec": {
                                                "command": ["/bin/sh", "-c", "ray stop"]
                                            }
                                        }
                                    },
                                    "name": "ray-head",
                                    "ports": [
                                        {
                                            "containerPort": 6379,
                                            "name": "gcs",
                                            "protocol": "TCP",
                                        },
                                        {
                                            "containerPort": 8265,
                                            "name": "dashboard",
                                            "protocol": "TCP",
                                        },
                                        {
                                            "containerPort": 10001,
                                            "name": "client",
                                            "protocol": "TCP",
                                        },
                                    ],
                                    "resources": {
                                        "limits": {
                                            "cpu": 2,
                                            "memory": "8G",
                                            "nvidia.com/gpu": 0,
                                        },
                                        "requests": {
                                            "cpu": 2,
                                            "memory": "8G",
                                            "nvidia.com/gpu": 0,
                                        },
                                    },
                                }
                            ]
                        }
                    },
                },
                "rayVersion": "1.12.0",
                "workerGroupSpecs": [
                    {
                        "groupName": "small-group-quicktest",
                        "maxReplicas": 1,
                        "minReplicas": 1,
                        "rayStartParams": {"block": "true", "num-gpus": "0"},
                        "replicas": 1,
                        "template": {
                            "metadata": {
                                "annotations": {"key": "value"},
                                "labels": {"key": "value"},
                            },
                            "spec": {
                                "containers": [
                                    {
                                        "env": [
                                            {
                                                "name": "MY_POD_IP",
                                                "valueFrom": {
                                                    "fieldRef": {
                                                        "fieldPath": "status.podIP"
                                                    }
                                                },
                                            }
                                        ],
                                        "image": "ghcr.io/foundation-model-stack/base:ray2.1.0-py38-gpu-pytorch1.12.0cu116-20221213-193103",
                                        "lifecycle": {
                                            "preStop": {
                                                "exec": {
                                                    "command": [
                                                        "/bin/sh",
                                                        "-c",
                                                        "ray stop",
                                                    ]
                                                }
                                            }
                                        },
                                        "name": "machine-learning",
                                        "resources": {
                                            "limits": {
                                                "cpu": 1,
                                                "memory": "2G",
                                                "nvidia.com/gpu": 0,
                                            },
                                            "requests": {
                                                "cpu": 1,
                                                "memory": "2G",
                                                "nvidia.com/gpu": 0,
                                            },
                                        },
                                    }
                                ],
                                "initContainers": [
                                    {
                                        "command": [
                                            "sh",
                                            "-c",
                                            "until nslookup $RAY_IP.$(cat /var/run/secrets/kubernetes.io/serviceaccount/namespace).svc.cluster.local; do echo waiting for myservice; sleep 2; done",
                                        ],
                                        "image": "busybox:1.28",
                                        "name": "init-myservice",
                                    }
                                ],
                            },
                        },
                    }
                ],
            },
            "status": {
                "availableWorkerReplicas": 2,
                "desiredWorkerReplicas": 1,
                "endpoints": {"client": "10001", "dashboard": "8265", "gcs": "6379"},
                "lastUpdateTime": "2023-02-22T16:26:16Z",
                "maxWorkerReplicas": 1,
                "minWorkerReplicas": 1,
                "state": "ready",
            },
        }
    )
    return [api_obj]


def get_aw_obj():
    api_obj1 = openshift.apiobject.APIObject(
        {
            "apiVersion": "mcad.ibm.com/v1beta1",
            "kind": "AppWrapper",
            "metadata": {
                "annotations": {
                    "kubectl.kubernetes.io/last-applied-configuration": '{"apiVersion":"mcad.ibm.com/v1beta1","kind":"AppWrapper","metadata":{"annotations":{},"name":"quicktest1","namespace":"ns"},"spec":{"priority":9,"resources":{"GenericItems":[{"custompodresources":[{"limits":{"cpu":2,"memory":"8G","nvidia.com/gpu":0},"replicas":1,"requests":{"cpu":2,"memory":"8G","nvidia.com/gpu":0}},{"limits":{"cpu":1,"memory":"2G","nvidia.com/gpu":0},"replicas":1,"requests":{"cpu":1,"memory":"2G","nvidia.com/gpu":0}}],"generictemplate":{"apiVersion":"ray.io/v1alpha1","kind":"RayCluster","metadata":{"labels":{"appwrapper.mcad.ibm.com":"quicktest1","controller-tools.k8s.io":"1.0"},"name":"quicktest1","namespace":"ns"},"spec":{"autoscalerOptions":{"idleTimeoutSeconds":60,"imagePullPolicy":"Always","resources":{"limits":{"cpu":"500m","memory":"512Mi"},"requests":{"cpu":"500m","memory":"512Mi"}},"upscalingMode":"Default"},"enableInTreeAutoscaling":false,"headGroupSpec":{"rayStartParams":{"block":"true","dashboard-host":"0.0.0.0","num-gpus":"0"},"serviceType":"ClusterIP","template":{"spec":{"containers":[{"image":"ghcr.io/foundation-model-stack/base:ray2.1.0-py38-gpu-pytorch1.12.0cu116-20221213-193103","imagePullPolicy":"Always","lifecycle":{"preStop":{"exec":{"command":["/bin/sh","-c","ray stop"]}}},"name":"ray-head","ports":[{"containerPort":6379,"name":"gcs"},{"containerPort":8265,"name":"dashboard"},{"containerPort":10001,"name":"client"}],"resources":{"limits":{"cpu":2,"memory":"8G","nvidia.com/gpu":0},"requests":{"cpu":2,"memory":"8G","nvidia.com/gpu":0}}}]}}},"rayVersion":"1.12.0","workerGroupSpecs":[{"groupName":"small-group-quicktest","maxReplicas":1,"minReplicas":1,"rayStartParams":{"block":"true","num-gpus":"0"},"replicas":1,"template":{"metadata":{"annotations":{"key":"value"},"labels":{"key":"value"}},"spec":{"containers":[{"env":[{"name":"MY_POD_IP","valueFrom":{"fieldRef":{"fieldPath":"status.podIP"}}}],"image":"ghcr.io/foundation-model-stack/base:ray2.1.0-py38-gpu-pytorch1.12.0cu116-20221213-193103","lifecycle":{"preStop":{"exec":{"command":["/bin/sh","-c","ray stop"]}}},"name":"machine-learning","resources":{"limits":{"cpu":1,"memory":"2G","nvidia.com/gpu":0},"requests":{"cpu":1,"memory":"2G","nvidia.com/gpu":0}}}],"initContainers":[{"command":["sh","-c","until nslookup $RAY_IP.$(cat /var/run/secrets/kubernetes.io/serviceaccount/namespace).svc.cluster.local; do echo waiting for myservice; sleep 2; done"],"image":"busybox:1.28","name":"init-myservice"}]}}}]}},"replicas":1},{"generictemplate":{"apiVersion":"route.openshift.io/v1","kind":"Route","metadata":{"labels":{"odh-ray-cluster-service":"quicktest-head-svc"},"name":"ray-dashboard-quicktest","namespace":"default"},"spec":{"port":{"targetPort":"dashboard"},"to":{"kind":"Service","name":"quicktest-head-svc"}}},"replica":1}],"Items":[]}}}\n'
                },
                "creationTimestamp": "2023-02-22T16:26:07Z",
                "generation": 4,
                "managedFields": [
                    {
                        "apiVersion": "mcad.ibm.com/v1beta1",
                        "fieldsType": "FieldsV1",
                        "fieldsV1": {
                            "f:spec": {
                                "f:resources": {"f:GenericItems": {}, "f:metadata": {}},
                                "f:schedulingSpec": {},
                                "f:service": {".": {}, "f:spec": {}},
                            },
                            "f:status": {
                                ".": {},
                                "f:canrun": {},
                                "f:conditions": {},
                                "f:controllerfirsttimestamp": {},
                                "f:filterignore": {},
                                "f:queuejobstate": {},
                                "f:sender": {},
                                "f:state": {},
                                "f:systempriority": {},
                            },
                        },
                        "manager": "Go-http-client",
                        "operation": "Update",
                        "time": "2023-02-22T16:26:07Z",
                    },
                    {
                        "apiVersion": "mcad.ibm.com/v1beta1",
                        "fieldsType": "FieldsV1",
                        "fieldsV1": {
                            "f:metadata": {
                                "f:annotations": {
                                    ".": {},
                                    "f:kubectl.kubernetes.io/last-applied-configuration": {},
                                }
                            },
                            "f:spec": {
                                ".": {},
                                "f:priority": {},
                                "f:resources": {".": {}, "f:Items": {}},
                            },
                        },
                        "manager": "kubectl-client-side-apply",
                        "operation": "Update",
                        "time": "2023-02-22T16:26:07Z",
                    },
                ],
                "name": "quicktest1",
                "namespace": "ns",
                "resourceVersion": "9482384",
                "uid": "6334fc1b-471e-4876-8e7b-0b2277679235",
            },
            "spec": {
                "priority": 9,
                "resources": {
                    "GenericItems": [
                        {
                            "allocated": 0,
                            "custompodresources": [
                                {
                                    "limits": {
                                        "cpu": "2",
                                        "memory": "8G",
                                        "nvidia.com/gpu": "0",
                                    },
                                    "replicas": 1,
                                    "requests": {
                                        "cpu": "2",
                                        "memory": "8G",
                                        "nvidia.com/gpu": "0",
                                    },
                                },
                                {
                                    "limits": {
                                        "cpu": "1",
                                        "memory": "2G",
                                        "nvidia.com/gpu": "0",
                                    },
                                    "replicas": 1,
                                    "requests": {
                                        "cpu": "1",
                                        "memory": "2G",
                                        "nvidia.com/gpu": "0",
                                    },
                                },
                            ],
                            "generictemplate": {
                                "apiVersion": "ray.io/v1alpha1",
                                "kind": "RayCluster",
                                "metadata": {
                                    "labels": {
                                        "appwrapper.mcad.ibm.com": "quicktest1",
                                        "controller-tools.k8s.io": "1.0",
                                    },
                                    "name": "quicktest1",
                                    "namespace": "ns",
                                },
                                "spec": {
                                    "autoscalerOptions": {
                                        "idleTimeoutSeconds": 60,
                                        "imagePullPolicy": "Always",
                                        "resources": {
                                            "limits": {
                                                "cpu": "500m",
                                                "memory": "512Mi",
                                            },
                                            "requests": {
                                                "cpu": "500m",
                                                "memory": "512Mi",
                                            },
                                        },
                                        "upscalingMode": "Default",
                                    },
                                    "enableInTreeAutoscaling": False,
                                    "headGroupSpec": {
                                        "rayStartParams": {
                                            "block": "true",
                                            "dashboard-host": "0.0.0.0",
                                            "num-gpus": "0",
                                        },
                                        "serviceType": "ClusterIP",
                                        "template": {
                                            "spec": {
                                                "containers": [
                                                    {
                                                        "image": "ghcr.io/foundation-model-stack/base:ray2.1.0-py38-gpu-pytorch1.12.0cu116-20221213-193103",
                                                        "imagePullPolicy": "Always",
                                                        "lifecycle": {
                                                            "preStop": {
                                                                "exec": {
                                                                    "command": [
                                                                        "/bin/sh",
                                                                        "-c",
                                                                        "ray stop",
                                                                    ]
                                                                }
                                                            }
                                                        },
                                                        "name": "ray-head",
                                                        "ports": [
                                                            {
                                                                "containerPort": 6379,
                                                                "name": "gcs",
                                                            },
                                                            {
                                                                "containerPort": 8265,
                                                                "name": "dashboard",
                                                            },
                                                            {
                                                                "containerPort": 10001,
                                                                "name": "client",
                                                            },
                                                        ],
                                                        "resources": {
                                                            "limits": {
                                                                "cpu": 2,
                                                                "memory": "8G",
                                                                "nvidia.com/gpu": 0,
                                                            },
                                                            "requests": {
                                                                "cpu": 2,
                                                                "memory": "8G",
                                                                "nvidia.com/gpu": 0,
                                                            },
                                                        },
                                                    }
                                                ]
                                            }
                                        },
                                    },
                                    "rayVersion": "1.12.0",
                                    "workerGroupSpecs": [
                                        {
                                            "groupName": "small-group-quicktest",
                                            "maxReplicas": 1,
                                            "minReplicas": 1,
                                            "rayStartParams": {
                                                "block": "true",
                                                "num-gpus": "0",
                                            },
                                            "replicas": 1,
                                            "template": {
                                                "metadata": {
                                                    "annotations": {"key": "value"},
                                                    "labels": {"key": "value"},
                                                },
                                                "spec": {
                                                    "containers": [
                                                        {
                                                            "env": [
                                                                {
                                                                    "name": "MY_POD_IP",
                                                                    "valueFrom": {
                                                                        "fieldRef": {
                                                                            "fieldPath": "status.podIP"
                                                                        }
                                                                    },
                                                                }
                                                            ],
                                                            "image": "ghcr.io/foundation-model-stack/base:ray2.1.0-py38-gpu-pytorch1.12.0cu116-20221213-193103",
                                                            "lifecycle": {
                                                                "preStop": {
                                                                    "exec": {
                                                                        "command": [
                                                                            "/bin/sh",
                                                                            "-c",
                                                                            "ray stop",
                                                                        ]
                                                                    }
                                                                }
                                                            },
                                                            "name": "machine-learning",
                                                            "resources": {
                                                                "limits": {
                                                                    "cpu": 1,
                                                                    "memory": "2G",
                                                                    "nvidia.com/gpu": 0,
                                                                },
                                                                "requests": {
                                                                    "cpu": 1,
                                                                    "memory": "2G",
                                                                    "nvidia.com/gpu": 0,
                                                                },
                                                            },
                                                        }
                                                    ],
                                                    "initContainers": [
                                                        {
                                                            "command": [
                                                                "sh",
                                                                "-c",
                                                                "until nslookup $RAY_IP.$(cat /var/run/secrets/kubernetes.io/serviceaccount/namespace).svc.cluster.local; do echo waiting for myservice; sleep 2; done",
                                                            ],
                                                            "image": "busybox:1.28",
                                                            "name": "init-myservice",
                                                        }
                                                    ],
                                                },
                                            },
                                        }
                                    ],
                                },
                            },
                            "metadata": {},
                            "priority": 0,
                            "priorityslope": 0,
                            "replicas": 1,
                        },
                        {
                            "allocated": 0,
                            "generictemplate": {
                                "apiVersion": "route.openshift.io/v1",
                                "kind": "Route",
                                "metadata": {
                                    "labels": {
                                        "odh-ray-cluster-service": "quicktest-head-svc"
                                    },
                                    "name": "ray-dashboard-quicktest",
                                    "namespace": "default",
                                },
                                "spec": {
                                    "port": {"targetPort": "dashboard"},
                                    "to": {
                                        "kind": "Service",
                                        "name": "quicktest-head-svc",
                                    },
                                },
                            },
                            "metadata": {},
                            "priority": 0,
                            "priorityslope": 0,
                        },
                    ],
                    "Items": [],
                    "metadata": {},
                },
                "schedulingSpec": {},
                "service": {"spec": {}},
            },
            "status": {
                "canrun": True,
                "conditions": [
                    {
                        "lastTransitionMicroTime": "2023-02-22T16:26:07.559447Z",
                        "lastUpdateMicroTime": "2023-02-22T16:26:07.559447Z",
                        "status": "True",
                        "type": "Init",
                    },
                    {
                        "lastTransitionMicroTime": "2023-02-22T16:26:07.559551Z",
                        "lastUpdateMicroTime": "2023-02-22T16:26:07.559551Z",
                        "reason": "AwaitingHeadOfLine",
                        "status": "True",
                        "type": "Queueing",
                    },
                    {
                        "lastTransitionMicroTime": "2023-02-22T16:26:13.220564Z",
                        "lastUpdateMicroTime": "2023-02-22T16:26:13.220564Z",
                        "reason": "AppWrapperRunnable",
                        "status": "True",
                        "type": "Dispatched",
                    },
                ],
                "controllerfirsttimestamp": "2023-02-22T16:26:07.559447Z",
                "filterignore": True,
                "queuejobstate": "Dispatched",
                "sender": "before manageQueueJob - afterEtcdDispatching",
                "state": "Running",
                "systempriority": 9,
            },
        }
    )
    api_obj2 = openshift.apiobject.APIObject(
        {
            "apiVersion": "mcad.ibm.com/v1beta1",
            "kind": "AppWrapper",
            "metadata": {
                "annotations": {
                    "kubectl.kubernetes.io/last-applied-configuration": '{"apiVersion":"mcad.ibm.com/v1beta1","kind":"AppWrapper","metadata":{"annotations":{},"name":"quicktest2","namespace":"ns"},"spec":{"priority":9,"resources":{"GenericItems":[{"custompodresources":[{"limits":{"cpu":2,"memory":"8G","nvidia.com/gpu":0},"replicas":1,"requests":{"cpu":2,"memory":"8G","nvidia.com/gpu":0}},{"limits":{"cpu":1,"memory":"2G","nvidia.com/gpu":0},"replicas":1,"requests":{"cpu":1,"memory":"2G","nvidia.com/gpu":0}}],"generictemplate":{"apiVersion":"ray.io/v1alpha1","kind":"RayCluster","metadata":{"labels":{"appwrapper.mcad.ibm.com":"quicktest2","controller-tools.k8s.io":"1.0"},"name":"quicktest2","namespace":"ns"},"spec":{"autoscalerOptions":{"idleTimeoutSeconds":60,"imagePullPolicy":"Always","resources":{"limits":{"cpu":"500m","memory":"512Mi"},"requests":{"cpu":"500m","memory":"512Mi"}},"upscalingMode":"Default"},"enableInTreeAutoscaling":false,"headGroupSpec":{"rayStartParams":{"block":"true","dashboard-host":"0.0.0.0","num-gpus":"0"},"serviceType":"ClusterIP","template":{"spec":{"containers":[{"image":"ghcr.io/foundation-model-stack/base:ray2.1.0-py38-gpu-pytorch1.12.0cu116-20221213-193103","imagePullPolicy":"Always","lifecycle":{"preStop":{"exec":{"command":["/bin/sh","-c","ray stop"]}}},"name":"ray-head","ports":[{"containerPort":6379,"name":"gcs"},{"containerPort":8265,"name":"dashboard"},{"containerPort":10001,"name":"client"}],"resources":{"limits":{"cpu":2,"memory":"8G","nvidia.com/gpu":0},"requests":{"cpu":2,"memory":"8G","nvidia.com/gpu":0}}}]}}},"rayVersion":"1.12.0","workerGroupSpecs":[{"groupName":"small-group-quicktest","maxReplicas":1,"minReplicas":1,"rayStartParams":{"block":"true","num-gpus":"0"},"replicas":1,"template":{"metadata":{"annotations":{"key":"value"},"labels":{"key":"value"}},"spec":{"containers":[{"env":[{"name":"MY_POD_IP","valueFrom":{"fieldRef":{"fieldPath":"status.podIP"}}}],"image":"ghcr.io/foundation-model-stack/base:ray2.1.0-py38-gpu-pytorch1.12.0cu116-20221213-193103","lifecycle":{"preStop":{"exec":{"command":["/bin/sh","-c","ray stop"]}}},"name":"machine-learning","resources":{"limits":{"cpu":1,"memory":"2G","nvidia.com/gpu":0},"requests":{"cpu":1,"memory":"2G","nvidia.com/gpu":0}}}],"initContainers":[{"command":["sh","-c","until nslookup $RAY_IP.$(cat /var/run/secrets/kubernetes.io/serviceaccount/namespace).svc.cluster.local; do echo waiting for myservice; sleep 2; done"],"image":"busybox:1.28","name":"init-myservice"}]}}}]}},"replicas":1},{"generictemplate":{"apiVersion":"route.openshift.io/v1","kind":"Route","metadata":{"labels":{"odh-ray-cluster-service":"quicktest-head-svc"},"name":"ray-dashboard-quicktest","namespace":"default"},"spec":{"port":{"targetPort":"dashboard"},"to":{"kind":"Service","name":"quicktest-head-svc"}}},"replica":1}],"Items":[]}}}\n'
                },
                "creationTimestamp": "2023-02-22T16:26:07Z",
                "generation": 4,
                "managedFields": [
                    {
                        "apiVersion": "mcad.ibm.com/v1beta1",
                        "fieldsType": "FieldsV1",
                        "fieldsV1": {
                            "f:spec": {
                                "f:resources": {"f:GenericItems": {}, "f:metadata": {}},
                                "f:schedulingSpec": {},
                                "f:service": {".": {}, "f:spec": {}},
                            },
                            "f:status": {
                                ".": {},
                                "f:canrun": {},
                                "f:conditions": {},
                                "f:controllerfirsttimestamp": {},
                                "f:filterignore": {},
                                "f:queuejobstate": {},
                                "f:sender": {},
                                "f:state": {},
                                "f:systempriority": {},
                            },
                        },
                        "manager": "Go-http-client",
                        "operation": "Update",
                        "time": "2023-02-22T16:26:07Z",
                    },
                    {
                        "apiVersion": "mcad.ibm.com/v1beta1",
                        "fieldsType": "FieldsV1",
                        "fieldsV1": {
                            "f:metadata": {
                                "f:annotations": {
                                    ".": {},
                                    "f:kubectl.kubernetes.io/last-applied-configuration": {},
                                }
                            },
                            "f:spec": {
                                ".": {},
                                "f:priority": {},
                                "f:resources": {".": {}, "f:Items": {}},
                            },
                        },
                        "manager": "kubectl-client-side-apply",
                        "operation": "Update",
                        "time": "2023-02-22T16:26:07Z",
                    },
                ],
                "name": "quicktest2",
                "namespace": "ns",
                "resourceVersion": "9482384",
                "uid": "6334fc1b-471e-4876-8e7b-0b2277679235",
            },
            "spec": {
                "priority": 9,
                "resources": {
                    "GenericItems": [
                        {
                            "allocated": 0,
                            "custompodresources": [
                                {
                                    "limits": {
                                        "cpu": "2",
                                        "memory": "8G",
                                        "nvidia.com/gpu": "0",
                                    },
                                    "replicas": 1,
                                    "requests": {
                                        "cpu": "2",
                                        "memory": "8G",
                                        "nvidia.com/gpu": "0",
                                    },
                                },
                                {
                                    "limits": {
                                        "cpu": "1",
                                        "memory": "2G",
                                        "nvidia.com/gpu": "0",
                                    },
                                    "replicas": 1,
                                    "requests": {
                                        "cpu": "1",
                                        "memory": "2G",
                                        "nvidia.com/gpu": "0",
                                    },
                                },
                            ],
                            "generictemplate": {
                                "apiVersion": "ray.io/v1alpha1",
                                "kind": "RayCluster",
                                "metadata": {
                                    "labels": {
                                        "appwrapper.mcad.ibm.com": "quicktest2",
                                        "controller-tools.k8s.io": "1.0",
                                    },
                                    "name": "quicktest2",
                                    "namespace": "ns",
                                },
                                "spec": {
                                    "autoscalerOptions": {
                                        "idleTimeoutSeconds": 60,
                                        "imagePullPolicy": "Always",
                                        "resources": {
                                            "limits": {
                                                "cpu": "500m",
                                                "memory": "512Mi",
                                            },
                                            "requests": {
                                                "cpu": "500m",
                                                "memory": "512Mi",
                                            },
                                        },
                                        "upscalingMode": "Default",
                                    },
                                    "enableInTreeAutoscaling": False,
                                    "headGroupSpec": {
                                        "rayStartParams": {
                                            "block": "true",
                                            "dashboard-host": "0.0.0.0",
                                            "num-gpus": "0",
                                        },
                                        "serviceType": "ClusterIP",
                                        "template": {
                                            "spec": {
                                                "containers": [
                                                    {
                                                        "image": "ghcr.io/foundation-model-stack/base:ray2.1.0-py38-gpu-pytorch1.12.0cu116-20221213-193103",
                                                        "imagePullPolicy": "Always",
                                                        "lifecycle": {
                                                            "preStop": {
                                                                "exec": {
                                                                    "command": [
                                                                        "/bin/sh",
                                                                        "-c",
                                                                        "ray stop",
                                                                    ]
                                                                }
                                                            }
                                                        },
                                                        "name": "ray-head",
                                                        "ports": [
                                                            {
                                                                "containerPort": 6379,
                                                                "name": "gcs",
                                                            },
                                                            {
                                                                "containerPort": 8265,
                                                                "name": "dashboard",
                                                            },
                                                            {
                                                                "containerPort": 10001,
                                                                "name": "client",
                                                            },
                                                        ],
                                                        "resources": {
                                                            "limits": {
                                                                "cpu": 2,
                                                                "memory": "8G",
                                                                "nvidia.com/gpu": 0,
                                                            },
                                                            "requests": {
                                                                "cpu": 2,
                                                                "memory": "8G",
                                                                "nvidia.com/gpu": 0,
                                                            },
                                                        },
                                                    }
                                                ]
                                            }
                                        },
                                    },
                                    "rayVersion": "1.12.0",
                                    "workerGroupSpecs": [
                                        {
                                            "groupName": "small-group-quicktest",
                                            "maxReplicas": 1,
                                            "minReplicas": 1,
                                            "rayStartParams": {
                                                "block": "true",
                                                "num-gpus": "0",
                                            },
                                            "replicas": 1,
                                            "template": {
                                                "metadata": {
                                                    "annotations": {"key": "value"},
                                                    "labels": {"key": "value"},
                                                },
                                                "spec": {
                                                    "containers": [
                                                        {
                                                            "env": [
                                                                {
                                                                    "name": "MY_POD_IP",
                                                                    "valueFrom": {
                                                                        "fieldRef": {
                                                                            "fieldPath": "status.podIP"
                                                                        }
                                                                    },
                                                                }
                                                            ],
                                                            "image": "ghcr.io/foundation-model-stack/base:ray2.1.0-py38-gpu-pytorch1.12.0cu116-20221213-193103",
                                                            "lifecycle": {
                                                                "preStop": {
                                                                    "exec": {
                                                                        "command": [
                                                                            "/bin/sh",
                                                                            "-c",
                                                                            "ray stop",
                                                                        ]
                                                                    }
                                                                }
                                                            },
                                                            "name": "machine-learning",
                                                            "resources": {
                                                                "limits": {
                                                                    "cpu": 1,
                                                                    "memory": "2G",
                                                                    "nvidia.com/gpu": 0,
                                                                },
                                                                "requests": {
                                                                    "cpu": 1,
                                                                    "memory": "2G",
                                                                    "nvidia.com/gpu": 0,
                                                                },
                                                            },
                                                        }
                                                    ],
                                                    "initContainers": [
                                                        {
                                                            "command": [
                                                                "sh",
                                                                "-c",
                                                                "until nslookup $RAY_IP.$(cat /var/run/secrets/kubernetes.io/serviceaccount/namespace).svc.cluster.local; do echo waiting for myservice; sleep 2; done",
                                                            ],
                                                            "image": "busybox:1.28",
                                                            "name": "init-myservice",
                                                        }
                                                    ],
                                                },
                                            },
                                        }
                                    ],
                                },
                            },
                            "metadata": {},
                            "priority": 0,
                            "priorityslope": 0,
                            "replicas": 1,
                        },
                        {
                            "allocated": 0,
                            "generictemplate": {
                                "apiVersion": "route.openshift.io/v1",
                                "kind": "Route",
                                "metadata": {
                                    "labels": {
                                        "odh-ray-cluster-service": "quicktest-head-svc"
                                    },
                                    "name": "ray-dashboard-quicktest",
                                    "namespace": "default",
                                },
                                "spec": {
                                    "port": {"targetPort": "dashboard"},
                                    "to": {
                                        "kind": "Service",
                                        "name": "quicktest-head-svc",
                                    },
                                },
                            },
                            "metadata": {},
                            "priority": 0,
                            "priorityslope": 0,
                        },
                    ],
                    "Items": [],
                    "metadata": {},
                },
                "schedulingSpec": {},
                "service": {"spec": {}},
            },
            "status": {
                "canrun": True,
                "conditions": [
                    {
                        "lastTransitionMicroTime": "2023-02-22T16:26:07.559447Z",
                        "lastUpdateMicroTime": "2023-02-22T16:26:07.559447Z",
                        "status": "True",
                        "type": "Init",
                    },
                    {
                        "lastTransitionMicroTime": "2023-02-22T16:26:07.559551Z",
                        "lastUpdateMicroTime": "2023-02-22T16:26:07.559551Z",
                        "reason": "AwaitingHeadOfLine",
                        "status": "True",
                        "type": "Queueing",
                    },
                    {
                        "lastTransitionMicroTime": "2023-02-22T16:26:13.220564Z",
                        "lastUpdateMicroTime": "2023-02-22T16:26:13.220564Z",
                        "reason": "AppWrapperRunnable",
                        "status": "True",
                        "type": "Dispatched",
                    },
                ],
                "controllerfirsttimestamp": "2023-02-22T16:26:07.559447Z",
                "filterignore": True,
                "queuejobstate": "Dispatched",
                "sender": "before manageQueueJob - afterEtcdDispatching",
                "state": "Pending",
                "systempriority": 9,
            },
        }
    )
    return [api_obj1, api_obj2]


def test_list_clusters(mocker, capsys):
    mocker.patch("openshift.selector", side_effect=get_selector)
    mock_res = mocker.patch.object(Selector, "objects")
    mock_res.side_effect = get_obj_none
    list_all_clusters("ns")
    captured = capsys.readouterr()
    assert captured.out == (
        "╭──────────────────────────────────────────────────────────────────────────────╮\n"
        "│ No resources found, have you run cluster.up() yet?                           │\n"
        "╰──────────────────────────────────────────────────────────────────────────────╯\n"
    )
    mock_res.side_effect = get_ray_obj
    list_all_clusters("ns")
    captured = capsys.readouterr()
    assert captured.out == (
        "                  🚀 CodeFlare Cluster Details 🚀                 \n"
        "                                                                  \n"
        " ╭──────────────────────────────────────────────────────────────╮ \n"
        " │   Name                                                       │ \n"
        " │   quicktest                                   Active ✅      │ \n"
        " │                                                              │ \n"
        " │   URI: ray://quicktest-head-svc.ns.svc:10001                 │ \n"
        " │                                                              │ \n"
        " │   Dashboard🔗                                                │ \n"
        " │                                                              │ \n"
        " │                      Cluster Resources                       │ \n"
        " │   ╭─ Workers ──╮  ╭───────── Worker specs(each) ─────────╮   │ \n"
        " │   │  Min  Max  │  │  Memory      CPU         GPU         │   │ \n"
        " │   │            │  │                                      │   │ \n"
        " │   │  1    1    │  │  2G~2G       1           0           │   │ \n"
        " │   │            │  │                                      │   │ \n"
        " │   ╰────────────╯  ╰──────────────────────────────────────╯   │ \n"
        " ╰──────────────────────────────────────────────────────────────╯ \n"
    )


def test_list_queue(mocker, capsys):
    mocker.patch("openshift.selector", side_effect=get_selector)
    mock_res = mocker.patch.object(Selector, "objects")
    mock_res.side_effect = get_obj_none
    list_all_queued("ns")
    captured = capsys.readouterr()
    assert captured.out == (
        "╭──────────────────────────────────────────────────────────────────────────────╮\n"
        "│ No resources found, have you run cluster.up() yet?                           │\n"
        "╰──────────────────────────────────────────────────────────────────────────────╯\n"
    )
    mock_res.side_effect = get_aw_obj
    list_all_queued("ns")
    captured = capsys.readouterr()
    assert captured.out == (
        "╭──────────────────────────╮\n"
        "│  🚀 Cluster Queue Status │\n"
        "│            🚀            │\n"
        "│ +------------+---------+ │\n"
        "│ | Name       | Status  | │\n"
        "│ +============+=========+ │\n"
        "│ | quicktest1 | running | │\n"
        "│ |            |         | │\n"
        "│ | quicktest2 | pending | │\n"
        "│ |            |         | │\n"
        "│ +------------+---------+ │\n"
        "╰──────────────────────────╯\n"
    )


def test_cluster_status(mocker):
    fake_aw = AppWrapper(
        "test", AppWrapperStatus.FAILED, can_run=True, job_state="unused"
    )
    fake_ray = RayCluster(
        name="test",
        status=RayClusterStatus.UNKNOWN,
        min_workers=1,
        max_workers=1,
        worker_mem_min=2,
        worker_mem_max=2,
        worker_cpu=1,
        worker_gpu=0,
        namespace="ns",
        dashboard="fake-uri",
    )
    cf = Cluster(ClusterConfiguration(name="test", namespace="ns"))
    status, ready = cf.status()
    assert status == CodeFlareClusterStatus.UNKNOWN
    assert ready == False

    mocker.patch(
        "codeflare_sdk.cluster.cluster._app_wrapper_status", return_value=fake_aw
    )
    status, ready = cf.status()
    assert status == CodeFlareClusterStatus.FAILED
    assert ready == False

    fake_aw.status = AppWrapperStatus.DELETED
    status, ready = cf.status()
    assert status == CodeFlareClusterStatus.FAILED
    assert ready == False

    fake_aw.status = AppWrapperStatus.PENDING
    status, ready = cf.status()
    assert status == CodeFlareClusterStatus.QUEUED
    assert ready == False

    fake_aw.status = AppWrapperStatus.COMPLETED
    status, ready = cf.status()
    assert status == CodeFlareClusterStatus.STARTING
    assert ready == False

    fake_aw.status = AppWrapperStatus.RUNNING_HOLD_COMPLETION
    status, ready = cf.status()
    assert status == CodeFlareClusterStatus.STARTING
    assert ready == False

    fake_aw.status = AppWrapperStatus.RUNNING
    status, ready = cf.status()
    assert status == CodeFlareClusterStatus.STARTING
    assert ready == False

    mocker.patch(
        "codeflare_sdk.cluster.cluster._ray_cluster_status", return_value=fake_ray
    )

    status, ready = cf.status()
    assert status == CodeFlareClusterStatus.STARTING
    assert ready == False

    fake_ray.status = RayClusterStatus.FAILED
    status, ready = cf.status()
    assert status == CodeFlareClusterStatus.FAILED
    assert ready == False

    fake_ray.status = RayClusterStatus.UNHEALTHY
    status, ready = cf.status()
    assert status == CodeFlareClusterStatus.FAILED
    assert ready == False

    fake_ray.status = RayClusterStatus.READY
    status, ready = cf.status()
    assert status == CodeFlareClusterStatus.READY
    assert ready == True


def test_wait_ready(mocker, capsys):
    cf = Cluster(ClusterConfiguration(name="test", namespace="ns"))
    try:
        cf.wait_ready(timeout=5)
        assert 1 == 0
    except Exception as e:
        assert type(e) == TimeoutError

    captured = capsys.readouterr()
    assert (
        "WARNING: Current cluster status is unknown, have you run cluster.up yet?"
        in captured.out
    )
    mocker.patch(
        "codeflare_sdk.cluster.cluster.Cluster.status",
        return_value=(True, CodeFlareClusterStatus.READY),
    )
    cf.wait_ready()
    captured = capsys.readouterr()
    assert (
        captured.out
        == "Waiting for requested resources to be set up...\nRequested cluster up and running!\n"
    )


def test_jobdefinition_coverage():
    abstract = JobDefinition()
    cluster = Cluster(test_config_creation())
    abstract._dry_run(cluster)
    abstract.submit(cluster)


def test_job_coverage():
    abstract = Job()
    abstract.status()
    abstract.logs()


def test_DDPJobDefinition_creation():
    ddp = DDPJobDefinition(
        script="test.py",
        m=None,
        script_args=["test"],
        name="test",
        cpu=1,
        gpu=0,
        memMB=1024,
        h=None,
        j="2x1",
        env={"test": "test"},
        max_retries=0,
        mounts=[],
        rdzv_port=29500,
        scheduler_args={"requirements": "test"},
    )
    assert ddp.script == "test.py"
    assert ddp.m == None
    assert ddp.script_args == ["test"]
    assert ddp.name == "test"
    assert ddp.cpu == 1
    assert ddp.gpu == 0
    assert ddp.memMB == 1024
    assert ddp.h == None
    assert ddp.j == "2x1"
    assert ddp.env == {"test": "test"}
    assert ddp.max_retries == 0
    assert ddp.mounts == []
    assert ddp.rdzv_port == 29500
    assert ddp.scheduler_args == {"requirements": "test"}
    return ddp


def test_DDPJobDefinition_dry_run():
    """
    Test that the dry run method returns the correct type: AppDryRunInfo,
    that the attributes of the returned object are of the correct type,
    and that the values from cluster and job definition are correctly passed.
    """
    ddp = test_DDPJobDefinition_creation()
    cluster = Cluster(test_config_creation())
    ddp_job = ddp._dry_run(cluster)
    assert type(ddp_job) == AppDryRunInfo
    assert ddp_job._fmt is not None
    assert type(ddp_job.request) == RayJob
    assert type(ddp_job._app) == AppDef
    assert type(ddp_job._cfg) == type(dict())
    assert type(ddp_job._scheduler) == type(str())

    assert ddp_job.request.app_id.startswith("test")
    assert ddp_job.request.cluster_name == "unit-test-cluster"
    assert ddp_job.request.requirements == "test"

    assert ddp_job._app.roles[0].resource.cpu == 1
    assert ddp_job._app.roles[0].resource.gpu == 0
    assert ddp_job._app.roles[0].resource.memMB == 1024

    assert ddp_job._cfg["cluster_name"] == "unit-test-cluster"
    assert ddp_job._cfg["requirements"] == "test"

    assert ddp_job._scheduler == "ray"


def test_DDPJobDefinition_dry_run_no_cluster(mocker):
    """
    Test that the dry run method returns the correct type: AppDryRunInfo,
    that the attributes of the returned object are of the correct type,
    and that the values from cluster and job definition are correctly passed.
    """

    mocker.patch(
        "openshift.get_project_name",
        return_value="opendatahub",
    )

    ddp = test_DDPJobDefinition_creation()
    ddp.image = "fake-image"
    ddp_job = ddp._dry_run_no_cluster()
    assert type(ddp_job) == AppDryRunInfo
    assert ddp_job._fmt is not None
    assert type(ddp_job.request) == KubernetesMCADJob
    assert type(ddp_job._app) == AppDef
    assert type(ddp_job._cfg) == type(dict())
    assert type(ddp_job._scheduler) == type(str())

    assert (
        ddp_job.request.resource["spec"]["resources"]["GenericItems"][0][
            "generictemplate"
        ]
        .spec.containers[0]
        .image
        == "fake-image"
    )

    assert ddp_job._app.roles[0].resource.cpu == 1
    assert ddp_job._app.roles[0].resource.gpu == 0
    assert ddp_job._app.roles[0].resource.memMB == 1024

    assert ddp_job._cfg["requirements"] == "test"

    assert ddp_job._scheduler == "kubernetes_mcad"


def test_DDPJobDefinition_dry_run_no_resource_args():
    """
    Test that the dry run correctly gets resources from the cluster object
    when the job definition does not specify resources.
    """
    cluster = Cluster(test_config_creation())
    ddp = DDPJobDefinition(
        script="test.py",
        m=None,
        script_args=["test"],
        name="test",
        h=None,
        env={"test": "test"},
        max_retries=0,
        mounts=[],
        rdzv_port=29500,
        scheduler_args={"requirements": "test"},
    )
    ddp_job = ddp._dry_run(cluster)

    assert ddp_job._app.roles[0].resource.cpu == cluster.config.max_cpus
    assert ddp_job._app.roles[0].resource.gpu == cluster.config.gpu
    assert ddp_job._app.roles[0].resource.memMB == cluster.config.max_memory * 1024
    assert (
        parse_j(ddp_job._app.roles[0].args[1])
        == f"{cluster.config.max_worker}x{cluster.config.gpu}"
    )


def test_DDPJobDefinition_dry_run_no_cluster_no_resource_args(mocker):
    """
    Test that the dry run method returns the correct type: AppDryRunInfo,
    that the attributes of the returned object are of the correct type,
    and that the values from cluster and job definition are correctly passed.
    """

    mocker.patch(
        "openshift.get_project_name",
        return_value="opendatahub",
    )

    ddp = test_DDPJobDefinition_creation()
    try:
        ddp._dry_run_no_cluster()
        assert 0 == 1
    except ValueError as e:
        assert str(e) == "Job definition missing arg: image"
    ddp.image = "fake-image"
    ddp.name = None
    try:
        ddp._dry_run_no_cluster()
        assert 0 == 1
    except ValueError as e:
        assert str(e) == "Job definition missing arg: name"
    ddp.name = "fake"
    ddp.cpu = None
    try:
        ddp._dry_run_no_cluster()
        assert 0 == 1
    except ValueError as e:
        assert str(e) == "Job definition missing arg: cpu (# cpus per worker)"
    ddp.cpu = 1
    ddp.gpu = None
    try:
        ddp._dry_run_no_cluster()
        assert 0 == 1
    except ValueError as e:
        assert str(e) == "Job definition missing arg: gpu (# gpus per worker)"
    ddp.gpu = 1
    ddp.memMB = None
    try:
        ddp._dry_run_no_cluster()
        assert 0 == 1
    except ValueError as e:
        assert str(e) == "Job definition missing arg: memMB (memory in MB)"
    ddp.memMB = 1
    ddp.j = None
    try:
        ddp._dry_run_no_cluster()
        assert 0 == 1
    except ValueError as e:
        assert str(e) == "Job definition missing arg: j (`workers`x`procs`)"


def test_DDPJobDefinition_submit(mocker):
    """
    Tests that the submit method returns the correct type: DDPJob
    And that the attributes of the returned object are of the correct type
    """
    ddp_def = test_DDPJobDefinition_creation()
    cluster = Cluster(test_config_creation())
    mocker.patch(
        "openshift.get_project_name",
        return_value="opendatahub",
    )
    mocker.patch(
        "codeflare_sdk.job.jobs.torchx_runner.schedule",
        return_value="fake-dashboard-url",
    )  # a fake app_handle
    ddp_job = ddp_def.submit(cluster)
    assert type(ddp_job) == DDPJob
    assert type(ddp_job.job_definition) == DDPJobDefinition
    assert type(ddp_job.cluster) == Cluster
    assert type(ddp_job._app_handle) == str
    assert ddp_job._app_handle == "fake-dashboard-url"

    ddp_def.image = "fake-image"
    ddp_job = ddp_def.submit()
    assert type(ddp_job) == DDPJob
    assert type(ddp_job.job_definition) == DDPJobDefinition
    assert ddp_job.cluster == None
    assert type(ddp_job._app_handle) == str
    assert ddp_job._app_handle == "fake-dashboard-url"


def test_DDPJob_creation(mocker):
    ddp_def = test_DDPJobDefinition_creation()
    cluster = Cluster(test_config_creation())
    mocker.patch(
        "codeflare_sdk.job.jobs.torchx_runner.schedule",
        return_value="fake-dashboard-url",
    )  # a fake app_handle
    ddp_job = DDPJob(ddp_def, cluster)
    assert type(ddp_job) == DDPJob
    assert type(ddp_job.job_definition) == DDPJobDefinition
    assert type(ddp_job.cluster) == Cluster
    assert type(ddp_job._app_handle) == str
    assert ddp_job._app_handle == "fake-dashboard-url"
    _, args, kwargs = torchx_runner.schedule.mock_calls[0]
    assert type(args[0]) == AppDryRunInfo
    job_info = args[0]
    assert type(job_info.request) == RayJob
    assert type(job_info._app) == AppDef
    assert type(job_info._cfg) == type(dict())
    assert type(job_info._scheduler) == type(str())
    return ddp_job


def test_DDPJob_creation_no_cluster(mocker):
    ddp_def = test_DDPJobDefinition_creation()
    ddp_def.image = "fake-image"
    mocker.patch(
        "openshift.get_project_name",
        return_value="opendatahub",
    )
    mocker.patch(
        "codeflare_sdk.job.jobs.torchx_runner.schedule",
        return_value="fake-app-handle",
    )  # a fake app_handle
    ddp_job = DDPJob(ddp_def, None)
    assert type(ddp_job) == DDPJob
    assert type(ddp_job.job_definition) == DDPJobDefinition
    assert ddp_job.cluster == None
    assert type(ddp_job._app_handle) == str
    assert ddp_job._app_handle == "fake-app-handle"
    _, args, kwargs = torchx_runner.schedule.mock_calls[0]
    assert type(args[0]) == AppDryRunInfo
    job_info = args[0]
    assert type(job_info.request) == KubernetesMCADJob
    assert type(job_info._app) == AppDef
    assert type(job_info._cfg) == type(dict())
    assert type(job_info._scheduler) == type(str())
    return ddp_job


def test_DDPJob_status(mocker):
    ddp_job = test_DDPJob_creation(mocker)
    mocker.patch(
        "codeflare_sdk.job.jobs.torchx_runner.status", return_value="fake-status"
    )
    assert ddp_job.status() == "fake-status"
    _, args, kwargs = torchx_runner.status.mock_calls[0]
    assert args[0] == "fake-dashboard-url"


def test_DDPJob_logs(mocker):
    ddp_job = test_DDPJob_creation(mocker)
    mocker.patch(
        "codeflare_sdk.job.jobs.torchx_runner.log_lines", return_value="fake-logs"
    )
    assert ddp_job.logs() == "fake-logs"
    _, args, kwargs = torchx_runner.log_lines.mock_calls[0]
    assert args[0] == "fake-dashboard-url"


def arg_check_side_effect(*args):
    assert args[0] == "fake-app-handle"


def test_DDPJob_cancel(mocker):
    ddp_job = test_DDPJob_creation_no_cluster(mocker)
    mocker.patch(
        "openshift.get_project_name",
        return_value="opendatahub",
    )
    mocker.patch(
        "codeflare_sdk.job.jobs.torchx_runner.cancel", side_effect=arg_check_side_effect
    )
    ddp_job.cancel()


def parse_j(cmd):
    pattern = r"--nnodes\s+\d+\s+--nproc_per_node\s+\d+"
    match = re.search(pattern, cmd)
    if match:
        substring = match.group(0)
    else:
        return None
    args = substring.split()
    max_worker = args[1]
    gpu = args[3]
    return f"{max_worker}x{gpu}"


def test_AWManager_creation():
    testaw = AWManager("test.yaml")
    assert testaw.name == "test"
    assert testaw.namespace == "ns"
    assert testaw.submitted == False
    try:
        testaw = AWManager("fake")
    except Exception as e:
        assert type(e) == FileNotFoundError
        assert str(e) == "[Errno 2] No such file or directory: 'fake'"
    try:
        testaw = AWManager("tests/test-case-bad.yaml")
    except Exception as e:
        assert type(e) == ValueError
        assert (
            str(e)
            == "tests/test-case-bad.yaml is not a correctly formatted AppWrapper yaml"
        )


def arg_check_aw_create_effect(*args):
    assert args[0] == "create"
    assert args[1] == ["-f", "test.yaml"]


def arg_check_aw_delete_effect(*args):
    assert args[0] == "delete"
    assert args[1] == ["AppWrapper", "test"]


def test_AWManager_submit_remove(mocker, capsys):
    testaw = AWManager("test.yaml")
    testaw.remove()
    captured = capsys.readouterr()
    assert (
        captured.out
        == "AppWrapper not submitted by this manager yet, nothing to remove\n"
    )
    assert testaw.submitted == False
    mocker.patch("openshift.invoke", side_effect=arg_check_aw_create_effect)
    testaw.submit()
    assert testaw.submitted == True
    mocker.patch("openshift.invoke", side_effect=arg_check_aw_delete_effect)
    testaw.remove()
    assert testaw.submitted == False


# Make sure to keep this function and the following function at the end of the file
def test_cmd_line_generation():
    os.system(
        f"python3 {parent}/src/codeflare_sdk/utils/generate_yaml.py --name=unit-cmd-cluster --min-cpu=1 --max-cpu=1 --min-memory=2 --max-memory=2 --gpu=1 --workers=2 --template=src/codeflare_sdk/templates/new-template.yaml"
    )
    assert filecmp.cmp(
        "unit-cmd-cluster.yaml", f"{parent}/tests/test-case-cmd.yaml", shallow=True
    )
    os.remove("unit-test-cluster.yaml")
    os.remove("unit-test-default-cluster.yaml")
    os.remove("unit-cmd-cluster.yaml")


# Make sure to always keep this function last
def test_cleanup():
    os.remove("test.yaml")
    os.remove("raytest2.yaml")
