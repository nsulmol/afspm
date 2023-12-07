"""Configuration for test_controller, adding option for config file."""

import pytest


def pytest_addoption(parser):
    parser.addoption("--config_path", action="store", default="./config.toml",
                     help="Path to config file, from which to load params.")


@pytest.fixture(scope="session")
def config_path(request):
    return request.config.getoption("--config_path")
