import launch
from importlib.metadata import PackageNotFoundError, version
from packaging.version import Version

try:
    requests_cache_version = Version(version("requests-cache"))
    requests_cache_supported = Version("1.2") <= requests_cache_version < Version("2")
except (PackageNotFoundError, ValueError):
    requests_cache_supported = False

if not requests_cache_supported:
    launch.run_pip('install "requests-cache>=1.2,<2"', "requests-cache")
