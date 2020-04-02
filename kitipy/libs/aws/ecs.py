"""This modules provides functions to implement a deployment pipeline for ECS.
The first part of this module is dedicated to loading and transforming task
definitions. Whereas the second part is a group of wrappers around boto3 SDK.
"""

import boto3
import datetime
import enum
import json
import kitipy
import mypy_boto3_ecs
import time
from container_transform.converter import Converter  # type: ignore
from typing import Callable, Dict, Generator, List, Literal, Optional, Tuple, TypedDict, Union

# Following list contains all the fields supported by create_service() but not
# by update_service().
create_update_diff = [
    'launchType',
    'assignPublicIp',
    'clientToken',
    'deploymentController',
    'enableECSManagedTags',
    'loadBalancers',
    'placementConstraints',
    'placementStrategy',
    'propagateTags',
    'serviceName',
    'serviceRegistries',
    'tags',
]


def convert_compose_to_ecs_config(compose_file: str) -> dict:
    """Convert a compose file into an ECS task definition.

    Args:
        compose_file (str): Path to the compose file to convert.

    Returns:
        dict: Contains the converted task definition.
    """
    converter = Converter(filename=compose_file,
                          input_type="compose",
                          output_type="ecs")
    converted = json.loads(converter.convert())
    return converted["containerDefinitions"]


def set_image_tag(containers: List[dict],
                  tag: str,
                  placeholder: str = "${IMAGE_TAG}") -> List[dict]:
    """Iterate over a list of containers and replace the given placeholder by
    a tag, in the containers' image parameter.

    Args:
        containers (List[dict]):
            A list of containers.
        tag (str):
            The image tag that should be set.
        placeholder (str):
            The placeholder to replace in containers' image parameter.
    
    Returns:
        List[dict]: The mutated list of containers.
    """
    return [
        dict(c, image=c["image"].replace(placeholder, tag)) for c in containers
    ]


def set_readonly_fs(containers: List[dict]) -> List[dict]:
    """Iterate over a list of containers and add the readonly parameter to all.

    Args:
        containers (List[dict]): A list of containers to transform.
    
    Returns:
        List[dict]: The list of transformed containers.
    """
    return [dict(c, readonlyRootFilesystem=True) for c in containers]


def add_secrets(containers: List[dict], secrets: dict) -> List[dict]:
    kctx = kitipy.get_current_context()
    by_name = {c["name"]: c for c in containers}

    for container, container_secrets in secrets.items():
        by_name[container].update({"secrets": container_secrets})

    return list(by_name.values())


def remove_containers(containers, excluding: Dict[str, str] = {}):
    return list(filter(lambda c: c["name"] in excluding, containers))


class ServiceNotFoundError(Exception):
    """ServiceNotFoundError is raised when trying to access an ECS service or
    one of its property (e.g. events or deployments) but the given service is
    not found."""
    pass


class DeploymentNotFoundError(Exception):
    """DeploymentNotFoundError is raised when looking for a deployment in a 
    service description but that deployment is not found."""
    pass


class ServiceDefinitionChangedError(Exception):
    """ServiceDefinitionChangedError is raised when trying to upsert a service
    but either its `loadBalancers` or `serviceRegistries` parameters don't
    match the parameters of the service currently deployed."""
    pass


def new_client() -> mypy_boto3_ecs.ECSClient:
    """Create a new boto3 ECS client.

    Returns:
        mypy_boto3_ecs.ECSClient: The API client.
    """
    return boto3.client("ecs")


def register_task_definition(client: mypy_boto3_ecs.ECSClient,
                             task_def: dict) -> str:
    """Register a task definition and returns its id.

    Args:
        client (mypy_boto3_ecs.ECSClient):
            An ECS API client.
        task_def (dict):
            A task definition as expected by ECS API. See https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task_definition_parameters.html.

    Returns:
        str: The definition ID in format "family:revision".
    """
    resp = client.register_task_definition(**task_def)
    task_def_id = "{0}:{1}".format(resp["taskDefinition"]["family"],
                                   resp["taskDefinition"]["revision"])

    # @TODO: use a proper logger
    kctx = kitipy.get_current_context()
    kctx.info(("A new task definition {task_def_id} " +
               "has been registered").format(task_def_id=task_def_id))

    return task_def_id


def upsert_service(client: mypy_boto3_ecs.ECSClient, cluster_name: str,
                   service_name: str, task_def: dict, service_def: dict) -> str:
    """Upsert an ECS service with its task definition.
    
    The desiredCount of the current service deployment is automatically reused.

    Args:
        client (mypy_boto3_ecs.ECSClient):
            An ECS API client.
        cluster_name (str):
            The name of the cluster where the service should be looked for.
        service_name (str):
            The name of the service to look for.
        task_def (dict):
            The task definition to register and deploy. See https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task_definition_parameters.html.
        service_def (dict):
            The definition of the service to upsert. See https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/ecs.html#ECS.Client.create_service.
    
    Returns:
        string: The ID of the service deployment.

    Raises:
        ServiceDefinitionChangedError:
            Both loadBalancers and serviceRegistries parameters from the
            service definitions can't be changed after creation. If you need to
            update these parameters, you should change the service name.
    """
    # @TODO: use a proper logger
    kctx = kitipy.get_current_context()

    task_def_id = register_task_definition(client, task_def)

    service_def["cluster"] = cluster_name
    service_def["serviceName"] = service_name
    service_def["taskDefinition"] = task_def_id

    if find_service_arn(client, cluster_name, service_name) is None:
        kctx.info(("Creating service {service} " +
                   "in {cluster} cluster.").format(service=service_name,
                                                   cluster=cluster_name))
        resp = client.create_service(**service_def)
        return resp["service"]["deployments"][0]["id"]

    existing = describe_service(client, cluster_name, service_name)

    if existing["loadBalancers"] != service_def.get("loadBalancers", []):
        raise ServiceDefinitionChangedError(
            "The parameter loadBalancers has changed.")

    if existing["serviceRegistries"] != service_def.get("serviceRegistries",
                                                        []):
        # @TODO: add previous/current values to the exception
        raise ServiceDefinitionChangedError(
            "The parameter serviceRegistries has changed.")

    # Remvoe all the params that are supported by create_service but not by
    # update_service.
    service_def["service"] = service_def["serviceName"]
    service_def = {
        k: v for k, v in service_def.items() if k not in create_update_diff
    }

    kctx.info(("Updating service {service} " + "in {cluster} cluster.").format(
        service=service_name, cluster=cluster_name))

    service_def["desiredCount"] = existing["desiredCount"]

    resp = client.update_service(**service_def)
    return resp["service"]["deployments"][0]["id"]


def run_oneoff_task(client: mypy_boto3_ecs.ECSClient, cluster_name: str,
                    task_name: str, task_def: dict, container: str,
                    command: List[str], run_args: dict) -> str:
    """Run a specific command in a oneoff ECS task.

    Args:
        client (mypy_boto3_ecs.ECSClient):
            An ECS API client.
        cluster_name (str):
            The name of the cluster where the task should run.
        task_name (str):
            The name of the task to create.
        task_def (dict):
            The task definition to register and deploy. See https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task_definition_parameters.html.
        container (str):
            The name of the container where the command should run.
        command (List[str]):
            The shell command to run in the container.
        run_args (dict):
            The list of arguments to pass to run_task(). See https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/ecs.html#ECS.Client.run_task.

    Returns:
        str: The ARN of the task.
    """
    # @TODO: use a proper logger
    kctx = kitipy.get_current_context()

    task_def_id = register_task_definition(client, task_def)

    run_args["cluster"] = cluster_name
    run_args["group"] = task_name
    run_args["taskDefinition"] = task_def_id
    run_args["count"] = 1
    run_args["overrides"] = {
        "containerOverrides": [{
            "name": container,
            "command": command
        }]
    }

    resp = client.run_task(**run_args)
    task_arn = resp["tasks"][0]["taskArn"]
    kctx.info("A new oneoff task {0} has been scheduled.".format(task_arn))

    return task_arn


def describe_service(
        client: mypy_boto3_ecs.ECSClient, cluster_name: str,
        service_name: str) -> mypy_boto3_ecs.type_defs.ServiceTypeDef:
    """Find the given service in the given cluster.

    Args:
        client (mypy_boto3_ecs.ECSClient):
            An ECS API client.
        cluster_name (str):
            The name of the cluster where the service should be looked for.
        service_name (str):
            The name of the service to look for.
    
    Returns:
        mypy_boto3_ecs.type_defs.ServiceTypeDef:
            The selected service.

    Raises:
        ServiceNotFoundError: When no matching service was found.
        RuntimeError: When more than 1 service have been returned by ECS API.
    """
    resp = client.describe_services(cluster=cluster_name,
                                    services=[service_name])
    services = resp["services"]

    if len(services) == 0:
        raise ServiceNotFoundError(
            "Service {0} not found.".format(service_name))
    elif len(services) != 1:
        raise RuntimeError("Expected 1 service in the list but got %d." %
                           (len(services)))

    return services[0]


def list_service_events(
    client: mypy_boto3_ecs.ECSClient, cluster_name: str, service_name: str
) -> List[mypy_boto3_ecs.type_defs.ServiceEventTypeDef]:
    """List the ECS events for a given service.

    Args:
        client (mypy_boto3_ecs.ECSClient):
            An ECS API client.
        cluster_name (str):
            The name of the cluster where the service should be looked for.
        service_name (str):
            The name of the service to look for.

    Returns:
        List[mypy_boto3_ecs.type_defs.
             ServiceEventTypeDef]:
            List of ECS events for the selected service.

    Raises:
        ServiceNotFoundError: When no matching service was found.
        RuntimeError: When more than 1 service have been returned by ECS API.
    """
    resp = describe_service(client, cluster_name, service_name)
    return resp["events"]


def find_service_deployments(
        client: mypy_boto3_ecs.ECSClient,
        cluster_name: str,
        service_name: str,
) -> List[mypy_boto3_ecs.type_defs.DeploymentTypeDef]:
    """List the deployments for a given service.

    Args:
        client (mypy_boto3_ecs.ECSClient):
            An ECS API client.
        cluster_name (str):
            The name of the cluster where the service should be looked for.
        service_name (str):
            The name of the service to look for.

    Returns:
        List[mypy_boto3_ecs.type_defs.
             ClientDescribeServiceResponseservicedeploymentsTypeDef]:
            List of deployments for the selected service.

    Raises:
        ServiceNotFoundError: When no matching service was found.
        RuntimeError: When more than 1 service have been returned by ECS API.
    """
    resp = describe_service(client, cluster_name, service_name)
    return resp["deployments"]


def find_service_deployment(
    client: mypy_boto3_ecs.ECSClient,
    cluster_name: str,
    service_name: str,
    filter_fn: Callable[[mypy_boto3_ecs.type_defs.DeploymentTypeDef], bool],
) -> Optional[mypy_boto3_ecs.type_defs.DeploymentTypeDef]:
    """Find a specific deployment for a given service.

    Args:
        client (mypy_boto3_ecs.ECSClient):
            An ECS API client.
        cluster_name (str):
            The name of the cluster where the service run.
        service_name (str):
            The name of the deployed service.
        filter_fn (Callable[[
            mypy_boto3_ecs.type_defs.
            DeploymentTypeDef
        ], bool]):
            The function called to find the desired deployment.

    Returns:
        Optional[mypy_boto3_ecs.type_defs.
                 ClientDescribeServiceResponseservicedeploymentsTypeDef]]:
            The service deployment if found, None otherwise.

    Raises:
        ServiceNotFoundError: When no matching service was found.
        RuntimeError: When more than 1 service have been returned by ECS API.
    """
    deployments = find_service_deployments(client, cluster_name, service_name)
    deployment = next((d for d in deployments if filter_fn(d)), None)

    return deployment


def get_primary_service_deployment(
        client: mypy_boto3_ecs.ECSClient, cluster_name: str,
        service_name: str) -> mypy_boto3_ecs.type_defs.DeploymentTypeDef:
    """Find the deployment with PRIMARY status for a given service.

    Args:
        client (mypy_boto3_ecs.ECSClient):
            An ECS API client.
        cluster_name (str):
            The name of the cluster where the service run.
        service_name (str):
            The name of the deployed service.

    Returns:
        mypy_boto3_ecs.type_defs.ClientDescribeServiceResponseservicedeploymentsTypeDef:
            The service deployment if found, None otherwise.

    Raises:
        ServiceNotFoundError:
            When no matching service was found.
        RuntimeError:
            When more than 1 service have been returned by ECS API.
        DeploymentNotFoundError:
            When no deployment with status PRIMARY is found for the given
            service.
    """
    d = find_service_deployment(client, cluster_name, service_name,
                                lambda d: d["status"] == "PRIMARY")

    if d is None:
        raise DeploymentNotFoundError(
            "Primary deployment not found for service {service}.".format(
                service=service_name))

    return d


def find_service_arn(client: mypy_boto3_ecs.ECSClient, cluster_name: str,
                     service_name: str) -> Optional[str]:
    """Find the ARN of a service.

    Args:
        client (mypy_boto3_ecs.ECSClient):
            An ECS API client.
        cluster_name (str):
            The name of the cluster where the service run.
        service_name (str):
            The name of the service to look for.

    Returns:
        Optional[str]:
            The service ARN if found, None otherwise.
    """
    list_resp = client.list_services(cluster=cluster_name)

    for arn in list_resp["serviceArns"]:
        if arn.endswith("service/" + service_name):
            return arn

    return None


def watch_deployment(
    client: mypy_boto3_ecs.ECSClient,
    cluster_name: str,
    service_name: str,
    deployment_id: str,
    max_attempts: int = 120,
) -> Generator[mypy_boto3_ecs.type_defs.ServiceEventTypeDef, None, None]:
    """Wait until a service deployment is complete and stream ECS events.

    This function polls the ECS API every 5s until the given deployment has
    completed. A deployment is completed once it has PRIMARY status and its
    number of desired replicas matches the running count.

    Args:
        client (mypy_boto3_ecs.ECSClient):
            An ECS API client.
        cluster_name (str):
            The name of the cluster where the service run.
        service_name (str):
            The name of the service to look for.
        deployment_id (str):
            The ID of the deployment to watch.
        max_attempts (number):
            The maximum number of attempts to be made. Default: 120 (~10 minutes).

    Raises:
        ServiceNotFoundError: When no matching service was found.
        RuntimeError: When more than 1 service have been returned by ECS API.
        DeploymentNotFoundError: When no deployment with the given ID is found.
        RuntimeError: When max_attempts is reached.
    """
    kctx = kitipy.get_current_context()
    status = None
    last_date = None
    attempts = 0.

    while attempts < max_attempts:
        deployment = find_service_deployment(client, cluster_name, service_name,
                                             lambda d: d["id"] == deployment_id)

        if deployment is None:
            raise DeploymentNotFoundError(
                "Deployment {0} not found.".format(deployment_id))

        if last_date is None:
            last_date = deployment["createdAt"]

        status = deployment["status"]
        events = list_service_events(client, cluster_name, service_name)
        new_events = list(e for e in events if e["createdAt"] > last_date)

        if len(new_events) > 0:
            last_date = new_events[0]["createdAt"]

        for event in reversed(new_events):
            yield event

        running_count = deployment["runningCount"]
        desired_count = deployment["desiredCount"]
        if status == "PRIMARY" and running_count == desired_count:
            return

        time.sleep(5)
        attempts += 1

    raise RuntimeError(
        "watch_deployment timed out before the deployment was completed. It is probably broken."
    )


def wait_until_task_stops(
        client: mypy_boto3_ecs.ECSClient, cluster_name: str,
        task_arn: str) -> mypy_boto3_ecs.type_defs.TaskTypeDef:
    """Wait until the given task reach STOPPED state.

    Args:
        client (mypy_boto3_ecs.ECSClient):
            An ECS API client.
        cluster_name (str):
            The name of the cluster where the task run.
        task_arn (str):
            The ARN of the ECS task to watch.

    Returns:
        mypy_boto3_ecs.type_defs.TaskTypeDef: The stopped task.
    """

    waiter = client.get_waiter('tasks_stopped')
    waiter.wait(cluster=cluster_name,
                tasks=[task_arn],
                WaiterConfig={
                    'Delay': 5,
                    'MaxAttempts': 120,
                })

    tasks = client.describe_tasks(tasks=[task_arn], cluster=cluster_name)
    return tasks['tasks'][0]


def get_task_definition(
    client: mypy_boto3_ecs.ECSClient, task_def_id: str
) -> mypy_boto3_ecs.type_defs.DescribeTaskDefinitionResponseTypeDef:
    """Describe a given task definition.

    Args:
        client (mypy_boto3_ecs.ECSClient):
            An ECS API client.
        task_def_id (str):
            The task definition to retrieve. This could be either just a family
            name or a specific revision, in the format "family:revision". In
            the former case, the latest ACTIVE definition is used.

    Returns:
        mypy_boto3_ecs.type_defs.DescribeTaskDefinitionResponseTypeDef:
            The task definition.
    """
    return client.describe_task_definition(taskDefinition=task_def_id,
                                           include=['TAGS'])


TaskDesiredStatus = Literal['RUNNING', 'PENDING', 'STOPPED']


class ListTasksFilters(TypedDict, total=False):
    containerInstance: str
    family: str
    startedBy: str
    serviceName: str
    desiredStatus: Union[List[TaskDesiredStatus], TaskDesiredStatus]
    launchType: str


def list_tasks(
    client: mypy_boto3_ecs.ECSClient, cluster_name: str,
    filters: ListTasksFilters, max_results: int
) -> Generator[mypy_boto3_ecs.type_defs.TaskTypeDef, None, None]:
    if isinstance(filters['desiredStatus'], str):
        filters['desiredStatus'] = [filters['desiredStatus']]

    for status in filters['desiredStatus']:
        args = dict(filters)
        args.update({
            'desiredStatus': status,
            'cluster': cluster_name,
            'maxResults': max_results,
        })

        list_resp = client.list_tasks(**args)  # type: ignore
        if len(list_resp['taskArns']) == 0:
            continue

        describe_resp = client.describe_tasks(tasks=list_resp['taskArns'],
                                              cluster=cluster_name)

        yield from describe_resp["tasks"]


ContainerInstanceStatus = Literal["ACTIVE", "DRAINING", "REGISTERING",
                                  "DEREGISTERING", "REGISTRATION_FAILED"]


def list_container_instances(
    client: mypy_boto3_ecs.ECSClient,
    cluster_name: str,
    status: ContainerInstanceStatus = 'ACTIVE',
    filter: Optional[str] = None,
) -> List[mypy_boto3_ecs.type_defs.ContainerInstanceTypeDef]:
    """List EC2 instances used by an ECS cluster.

    Args:
        client (mypy_boto3_ecs.ECSClient):
            An ECS API client.
        cluster_name (str):
            Name of the ECS cluster.
        status (ContainerInstanceStatus):
            Filters the container instances by status. Defaults to ACTIVE.
        filter (Optional[str]):
            You can filter the results of a ListContainerInstances operation
            with cluster query language statements.

    Returns:
        List[str]: ID of EC2 instances in the given ECS cluster.
    """
    args = {'cluster': cluster_name, 'status': status}
    if filter is not None:
        args['filter'] = filter

    instance_arns = client.list_container_instances(**args)

    if len(instance_arns['containerInstanceArns']) == 0:
        return []

    instances = client.describe_container_instances(
        cluster=cluster_name,
        containerInstances=instance_arns['containerInstanceArns'])

    return instances['containerInstances']


class ContainerInstanceNotFoundError(Exception):
    """ContainerInstanceNotFoundError is raised when trying to interact with a Container Instance but the
    given instance Group is not found."""
    pass


def describe_container_instance(client: mypy_boto3_ecs.Client,
                                cluster_name: str, instance_id: str):
    instances = client.describe_container_instances(
        cluster=cluster_name, containerInstances=instance_id)
    instance = next(instances['containerInstances'])

    if instance is None:
        raise ContainerInstanceNotFoundError(
            "Container instance {0} not found.".format(instance_id))

    return instance


def drain_container_instance(client: mypy_boto3_ecs.Client,
                             cluster_name: str,
                             container_instance: str,
                             wait_until_is_drained: bool = False):
    """Drain containers running on a specific instance.

    Args:
        client (mypy_boto3_ecs.ECSClient):
            An ECS API client.
        cluster_name (str):
            Name of the ECS cluster.
        container_instance (str):
            ARN of the ECS container instance to drain.
        wait_until_is_drained (bool):
            Whether this function should wait until the given instance has been
            drained before returning. Defaults to False.
    """
    client.update_container_instances_state(
        cluster=cluster_name,
        containerInstances=[container_instance],
        status='DRAINING')

    if wait_until_is_drained:
        wait_until_container_instance_is_drained(client, cluster_name,
                                                 container_instance)


def wait_until_container_instance_is_drained(client: mypy_boto3_ecs.Client,
                                             cluster_name: str,
                                             container_instance: str):

    def check_is_drained() -> bool:
        filters: ListTasksFilters = {
            'desiredStatus': 'RUNNING',
            'containerInstance': container_instance
        }
        tasks = list(list_tasks(client, cluster_name, filters, 1))
        return len(tasks) == 0

    attempts = 0
    max_attempts = 60
    interval = 10

    while attempts < max_attempts:
        if check_is_drained():
            return

        time.sleep(interval)
        attempts += 1

    raise RuntimeError(
        "wait_until_container_instance_is_drained timed out before all tasks running on the container instance {0} were drained."
        .format(container_instance))
