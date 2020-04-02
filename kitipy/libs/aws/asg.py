import boto3
import mypy_boto3_autoscaling
import mypy_boto3_autoscaling.type_defs
import time
from typing import Callable, List


def new_client() -> mypy_boto3_autoscaling.Client:
    """Create a new boto3 AutoScaling client.

    Returns:
        mypy_boto3_autoscaling.Client: The API client.
    """
    return boto3.client("autoscaling")


class ASGNotFoundError(Exception):
    """ASGNotFoundError is raised when trying to interact with an ASG but the
    given Auto Scaling Group is not found."""
    pass


def describe_auto_scaling_group(
    client: mypy_boto3_autoscaling.Client, asg_name: str
) -> mypy_boto3_autoscaling.type_defs.AutoScalingGroupTypeDef:
    """Describe a single Auto Scaling Group (utiliy function, as this is not
    provided by boto3).

    Args:
        client (mypy_boto3_autoscaling.Client):
            An AutoScaling API client.
        asg_name (str):
            Name of the Auto Scaling Group to roll.

    Raises:
        ASGNotFoundError: When no ASG with that name could be found.
    """
    resp = client.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
    asgs = resp['AutoScalingGroups']

    if len(asgs) == 0:
        raise RuntimeError("ASG {0} not found.".format(asg_name))

    return asgs[0]


def ec2_instance_id_matcher(ids: List[str]):
    """Create a new instance filter that could be used with roll_instances() to
    determine which EC2 instances should be replaced by matching instance IDs
    with the provided list.
    
    Args:
        id (List[str]):
            List of EC2 instance IDs that should be matched.

    Returns:
        Callable[[mypy_boto3_autoscaling.type_defs.InstanceTypeDef], bool]:
            The matcher function.
    """

    def matcher(
            instance: mypy_boto3_autoscaling.type_defs.InstanceTypeDef) -> bool:
        return instance['InstanceId'] in ids

    return matcher


def roll_instances(
    client: mypy_boto3_autoscaling.Client,
    asg: mypy_boto3_autoscaling.type_defs.AutoScalingGroupTypeDef,
    instance_filter: Callable[
        [mypy_boto3_autoscaling.type_defs.InstanceTypeDef],
        bool], drainer: Callable[[str], None],
    waiter: Callable[[mypy_boto3_autoscaling.type_defs.AutoScalingGroupTypeDef],
                     None]):
    """Roll instances in an Auto Scaling Group.

    This function starts by increasing the desired count by one and then
    iterates over the given list of EC2 instances to drain them and then
    stop them. Finally, it decreases the desired count by one. If an error
    happen, the desired count is decreased.

    Args:
        client (mypy_boto3_autoscaling.Client):
            An AutoScaling API client.
        asg (mypy_boto3_autoscaling.type_defs.AutoScalingGroupTypeDef):
            Name of the Auto Scaling Group to roll.
        instance_filter (Callable[[mypy_boto3_autoscaling.type_defs.InstanceTypeDef], bool]):
            Filter function that takes an ASG Instance and returns whether
            that instance should be rolled.
        drainer (Callable[[str], None]):
            Function called to drain a given EC2 instance.
        waiter (Callable[[mypy_boto3_autoscaling.type_defs.AutoScalingGroupTypeDef], None]):
            Function called to wait between two instance rollings.
            See all_instances_in_service_waiter().

    Raises:
        ASGNotFoundError: When no ASG with that name could be found.
    """
    instances = [
        instance for instance in asg['Instances'] if instance_filter(instance)
    ]
    if len(instances) == 0:
        return

    increase_desired_capacity(client, asg['AutoScalingGroupName'], 1)

    try:
        waiter(asg)

        for instance in instances:
            instance_id = instance['InstanceId']
            drainer(instance_id)

            client.terminate_instance_in_auto_scaling_group(
                InstanceId=instance_id, ShouldDecrementDesiredCapacity=False)

            waiter(asg)
    finally:
        decrease_desired_capacity(client, asg['AutoScalingGroupName'], 1)


def wait_until_all_instances_in_service(
    client: mypy_boto3_autoscaling.Client,
    asg: mypy_boto3_autoscaling.type_defs.AutoScalingGroupTypeDef):

    def check_desired_capacity(
        asg: mypy_boto3_autoscaling.type_defs.AutoScalingGroupTypeDef
    ) -> bool:
        updated_asg = describe_auto_scaling_group(client,
                                                  asg['AutoScalingGroupName'])
        in_service_instances = [
            i for i in updated_asg['Instances']
            if i['LifecycleState'] == 'InService'
        ]

        return updated_asg['DesiredCapacity'] == len(in_service_instances)

    attempts = 0
    max_attempts = 60
    interval = 5

    while attempts < max_attempts:
        if check_desired_capacity(asg):
            return

        time.sleep(interval)
        attempts += 1

    raise RuntimeError(
        "wait_until_all_instances_in_service timed out before all Container Instances reached InService state."
    )


def increase_desired_capacity(client: mypy_boto3_autoscaling.Client,
                              asg_name: str, addend: int):
    asg = describe_auto_scaling_group(client, asg_name)
    args = {
        'AutoScalingGroupName': asg_name,
        'DesiredCapacity': int(asg['DesiredCapacity']) + addend
    }

    client.update_auto_scaling_group(**args)


def decrease_desired_capacity(client: mypy_boto3_autoscaling.Client,
                              asg_name: str, subtrahend: int):
    asg = describe_auto_scaling_group(client, asg_name)
    args = {
        'AutoScalingGroupName': asg_name,
        'DesiredCapacity': int(asg['DesiredCapacity']) - subtrahend
    }

    client.update_auto_scaling_group(**args)
