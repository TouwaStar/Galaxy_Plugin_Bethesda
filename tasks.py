import os
import sys
import json
from shutil import rmtree, copy
from glob import glob
import platform
from invoke import task
from invoke.exceptions import Exit
import tempfile
import subprocess
from src.version import __version__


@task(aliases=["req"], optional=["locally"])
def requirements(c, locally="False"):
    """Install python requirements"""
    c.run(f"pip install -r requirements/dev.txt")


@task(optional=["output", "ziparchive"])
def build(c, output="output", ziparchive=None):
    """Build plugin executable"""
    from galaxy.tools import zip_folder_to_file  # non standard lib

    system = platform.system()
    env = {}
    if system == "Windows":
        pip_platform = "win32"
    elif system == "Darwin":
        pip_platform = "macosx_10_12_x86_64"
        env["MACOSX_DEPLOYMENT_TARGET"] = "10.12"  # for building from sources
    else:
        Exit("System {} not supported".format(system))

    if os.path.exists(output):
        rmtree(output)

    # compute requirements
    requirements = tempfile.NamedTemporaryFile(mode="w", delete=False)

    try:
        c.run("pip-compile requirements/app.txt --dry-run", out_stream=requirements)
        requirements.close()

        # install requirements
        args = [
            "pip install",
            "-r " + requirements.name,
            "--implementation cp",
            "--python-version 37",
            "--platform " + pip_platform,
            "--target " + output,
            "--no-compile",
            "--no-deps"
        ]
        c.run(" ".join(args), echo=True)
    finally:
        requirements.close()
        os.unlink(requirements.name)

    # clean .dist-info dirs
    for dir_ in glob("{}/*.dist-info".format(output)):
        rmtree(dir_)

    # copy src
    for file_ in glob("src/*.py"):
        copy(file_, output)

    # remove dependencies tests
    for test in glob(f"{output}/**/test_*.py".format(output), recursive=True):
        os.remove(test)
    for test in glob(f"{output}/**/*_test.py".format(output), recursive=True):
        os.remove(test)
    for test in glob(f"{output}/**/test.py".format(output), recursive=True):
        os.remove(test)

    # create manifest
    manifest = {
        "name": "Galaxy Bethesda plugin",
        "platform": "bethesda",
        "guid": "24o5f405-7271-4498-83g4-50a6f66b1dcf",
        "version": __version__,
        "description": "Galaxy Bethesda plugin",
        "author": "TouwaStar",
        "email": "yowosek@gmail.com",
        "url": "https://github.com/TouwaStar/Galaxy_plugin_bethesda",
        "script": "plugin.py"
    }
    with open(os.path.join(output, "manifest.json"), "w") as file_:
        json.dump(manifest, file_, indent=4)

    if ziparchive is not None:
        zip_folder_to_file(output, ziparchive)


@task
def test(c):
    """Run tests"""
    c.run("pytest --cache-clear --color=yes")


@task
def dist(c, source='output', version=9.9):
    """Copy source to where github plugins lands. For debugging purposes only"""

    import psutil
    if sys.platform == 'win32':
        galaxy = 'GalaxyClient.exe'
    elif sys.platform == 'darwin':
        galaxy = 'GOG Galaxy'
    galaxy_path = None
    for proc in psutil.process_iter(attrs=['exe'], ad_value=''):
        if proc.info['exe'].endswith(galaxy):
            galaxy_path = proc.info['exe']
            print(f'Galaxy at {galaxy_path} is running!. Terminating...')
            proc.terminate()
    else:
        if sys.platform == 'win32':
            galaxy_path = 'C:\Program Files (x86)\GOG Galaxy\GalaxyClient.exe'
        elif sys.platform == 'darwin':
            galaxy_path = "/Applications/GOG Galaxy.app/Contents/MacOS/GOG Galaxy"

    print(f'inv build -o {source}')
    c.run(f'inv build -o {source}')

    print('overwrites version to 9.9')
    with open(os.path.join(source, 'manifest.json'), 'r') as f:
        data = json.load(f)
        data['version'] = version
    with open(os.path.join(source, 'manifest.json'), 'w') as f:
        json.dump(data, f, indent=4)
    with open(os.path.join(source, 'version.py'), 'w') as f:
        f.write(f'__version__ = "{version}"')

    plugin_name = 'bethesda 24o5f405-7271-4498-83g4-50a6f66b1dcf'
    if sys.platform == 'win32':
        dest = rf'%localappdata%\GOG.com\Galaxy\plugins\installed\{plugin_name}'
        cp_cmd = f'xcopy /E/Y/i "{source}\*" {dest}'
    elif sys.platform == 'darwin':
        dest = f'~/Library/Application\ Support/GOG.com/Galaxy/plugins/installed/{plugin_name}'
        cp_cmd = f'mkdir -p {dest} && cp -rf {source}/* {dest}'
    print(cp_cmd)
    c.run(cp_cmd)

    print(f'Reopening Galaxy from {galaxy_path}')
    subprocess.run([galaxy_path, 'runWithoutUpdating', '&'])


@task(requirements, build, test)
def all(_):
    pass
