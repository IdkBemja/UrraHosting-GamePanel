import docker.errors
import pytest
from app.services.docker_client import DockerControlError, InstanceDockerClient


class _FakeContainer:
    def __init__(self, labels, attrs):
        self.labels = labels
        self.attrs = attrs
        self.started = self.stopped = self.restarted = False

    def reload(self):
        pass

    def start(self):
        self.started = True

    def stop(self, timeout=60):
        self.stopped = True

    def restart(self, timeout=60):
        self.restarted = True


class _FakeContainers:
    def __init__(self, container):
        self._container = container

    def get(self, name):
        if self._container is None:
            raise docker.errors.NotFound("not found")
        return self._container


class _FakeDockerClient:
    def __init__(self, container):
        self.containers = _FakeContainers(container)


def _client_with(container):
    instance = InstanceDockerClient("tcp://ignored:2375", "abc_game_runtime", "instance-1")
    instance._client = _FakeDockerClient(container)
    return instance


def test_status_rejects_wrong_instance_label():
    container = _FakeContainer(labels={"com.urrahosting.instance": "someone-else"}, attrs={"State": {}})
    client = _client_with(container)
    with pytest.raises(DockerControlError):
        client.status()


def test_status_returns_running_state():
    container = _FakeContainer(
        labels={"com.urrahosting.instance": "instance-1"},
        attrs={"State": {"Status": "running", "Running": True, "StartedAt": "2026-01-01T00:00:00Z"}},
    )
    client = _client_with(container)
    state = client.status()
    assert state["running"] is True
    assert state["status"] == "running"


def test_health_returns_none_without_healthcheck():
    container = _FakeContainer(labels={"com.urrahosting.instance": "instance-1"}, attrs={"State": {}})
    client = _client_with(container)
    assert client.health() is None


def test_health_returns_status_when_present():
    container = _FakeContainer(
        labels={"com.urrahosting.instance": "instance-1"},
        attrs={"State": {"Health": {"Status": "healthy"}}},
    )
    client = _client_with(container)
    assert client.health() == "healthy"


def test_container_not_found_raises():
    client = _client_with(None)
    with pytest.raises(DockerControlError):
        client.status()


def test_start_stop_restart_delegate_to_container():
    container = _FakeContainer(labels={"com.urrahosting.instance": "instance-1"}, attrs={"State": {}})
    client = _client_with(container)
    client.start()
    client.stop()
    client.restart()
    assert container.started and container.stopped and container.restarted
