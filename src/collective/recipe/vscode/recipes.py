# _*_ coding: utf-8 _*_
""" """
from collections import OrderedDict
from zc.buildout import UserError

import io
import json
import logging
import os
import re
import subprocess
import sys
import zc.recipe.egg


PY2 = sys.version_info[0] == 2

json_comment = re.compile(r"/\*.*?\*/", re.DOTALL | re.MULTILINE)
json_dump_params = {"sort_keys": True, "indent": 4, "separators": (",", ":")}
json_load_params = {}

python_file_defaults = {
    "files.associations": {"*.zcml": "xml"},
    "files.exclude": {"**/*.py[co]": True, "**/*.so": True, "**/__pycache__": True},
}

ROBOT_LSP_LAUNCH_TEMPLATE = lambda pythonpath: {
    "type": "robotframework-lsp",
    "name": "Robot Framework: Launch Template",
    "request": "launch",
    "cwd": "^\"\\${workspaceFolder}\"",
    "target": "^\"\\${file}\"",
    "terminal": "integrated",
    "env": {
        "LISTENER_HOST": "localhost",
        "LISTENER_PORT": 49999,
        "PYTHONPATH": pythonpath,
    },
    "args": [
        "--variable",
        "ZOPE_HOST:localhost",
        "--variable",
        "ZOPE_port:55001",
        "--listener",
        "plone.app.robotframework.server.RobotListener",
    ]
}

ROBOT_SERVER_TASK_TEMPLATE = {
    "label": "Start Plone Test Server",
    "type": "shell",
    "command": "ZSERVER_PORT=55001 bin/robot-server ${input:ploneTestingLayer} --no-reload -vv",
    "presentation": {
      "reveal": "always",
      "panel": "shared",
    },
    "problemMatcher": [],
}

ROBOT_SERVER_INPUT_TEMPLATE = {
    "id": "ploneTestingLayer",
    "type": "promptString",
    "description": "Enter Plone Testing Fixture",
    "default": "Products.CMFPlone.testing.PRODUCTS_CMFPLONE_ROBOT_TESTING"
}


def ensure_unicode(string):
    """" """
    u_string = string
    if isinstance(u_string, bytes):
        u_string = u_string.decode("utf-8", "strict")

    elif PY2 and isinstance(u_string, basestring):  # noqa: F821
        if not isinstance(u_string, unicode):  # noqa: F821
            u_string = u_string.decode("utf-8", "strict")

    return u_string


def find_executable_path(name):
    """ """
    try:
        path_ = subprocess.check_output(["which", name])
        return ensure_unicode(path_.strip())

    except subprocess.CalledProcessError:
        pass


with io.open(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings_mappings.json"),
    "r",
    encoding="utf-8",
) as f:
    mappings = json.loads(f.read())


class Recipe:

    """zc.buildout recipe for vscode project settings:
    """

    def __init__(self, buildout, name, options):
        """ """
        # keep original user provided options
        # make deep copy of zc.buildout.buildout.Options with dict
        self.user_options = dict(options)
        self.buildout, self.name, self.options = buildout, name, options
        self.logger = logging.getLogger(self.name)

        self._set_defaults()

        self.settings_dir = os.path.join(options["project-root"], ".vscode")
        if not os.path.exists(self.settings_dir):
            os.makedirs(self.settings_dir)

        develop_eggs = []

        if self.options["ignore-develop"].lower() in ("yes", "true", "on", "1", "sure"):

            develop_eggs = os.listdir(buildout["buildout"]["develop-eggs-directory"])
            develop_eggs = [dev_egg[:-9] for dev_egg in develop_eggs]

        ignores = options.get("ignores", "").split()
        self.ignored_eggs = develop_eggs + ignores

        self.packages = [
            p.strip() for p in self.options["packages"].splitlines() if p and p.strip()
        ]

        # Make all other recipes dependent on us so they run first to
        # ensure all implctly
        # referenced parts are loaded
        for part in self.buildout["buildout"].get("parts", "").split():
            self.buildout.get(part)

    def install(self):
        """Let's build vscode settings file:
        This is the method will be called by buildout it-self and this recipe
        will generate or/update vscode setting file (.vscode/settings.json) based
        on provided options.
        """

        if self.options.get("eggs"):
            # Need working set for all eggs and zc.recipe.egg also
            parts = [
                (self.name, self.options["recipe"], self.options),
                ("dummy", "zc.recipe.egg", {}),
            ]
        else:
            parts = []
            # get the parts including those not explicity in parts
            # TODO: is there a way without a private method?
            installed_part_options, _ = self.buildout._read_installed_part_options()
            for part, options in installed_part_options.items():
                if options is None or not options.get("recipe", None):
                    continue
                recipe = options["recipe"]
                if ":" in recipe:
                    recipe, _ = recipe.split(":")
                parts.append((part, recipe, options))

        eggs_locations = set()
        develop_eggs_locations = set()
        develop_eggs = os.listdir(self.buildout["buildout"]["develop-eggs-directory"])
        develop_eggs = [dev_egg[:-9] for dev_egg in develop_eggs]

        for part, recipe, options in parts:
            egg = zc.recipe.egg.Egg(self.buildout, recipe, options)
            try:
                _, ws = egg.working_set()
            except Exception as exc:  # noqa: B902
                raise UserError(str(exc))

            for dist in ws.by_key.values():

                project_name = dist.project_name
                if project_name not in self.ignored_eggs:
                    eggs_locations.add(dist.location)
                if project_name in develop_eggs:
                    develop_eggs_locations.add(dist.location)

            for package in self.packages:
                eggs_locations.add(package)

        try:
            with io.open(
                os.path.join(self.settings_dir, "settings.json"), "r", encoding="utf-8"
            ) as fp:
                json_text = fp.read()
                existing_settings = json.loads(json_text)

        except ValueError as e:
            raise UserError(str(e))
        except IOError:
            existing_settings = dict()

        vscode_settings = self._prepare_settings(
            list(eggs_locations), list(develop_eggs_locations), existing_settings
        )

        self._write_project_file(vscode_settings, existing_settings)

        # Write json file values only those are generated by this recipe.
        # Also dodges (by giving fake like file) buildout to
        # remove original settings.json file.
        vs_generated_file = os.path.join(
            self.settings_dir, "vs-recipe-generated-settings.json"
        )
        with io.open(vs_generated_file, "w", encoding="utf-8") as fp:
            json_text = json.dumps(vscode_settings, indent=2, sort_keys=True)
            fp.write(ensure_unicode(json_text))

        # Update .vscode/launch.js and .vscode/tasks.js for Robot testing
        if vscode_settings.get("robot.python.env"):
            vs_launch_file = os.path.join(self.settings_dir, "launch.json")
            if os.path.exists(vs_launch_file):
                with io.open(vs_launch_file, "r", encoding="utf-8") as fp:
                    launch_json = json.loads(fp.read())
            else:
                launch_json = dict(version="0.2.0")
            launch_json.setdefault("configurations", [])
            launch_json["configurations"] = [
                c for c in launch_json["configurations"]
                if c["type"] != "robotframwork-lsp" and
                c["name"] != "Robot Framework: Launch Template"
            ] + [
                ROBOT_LSP_LAUNCH_TEMPLATE(
                    vscode_settings["robot.python.env"]["PYTHONPATH"].replace(
                        '${PYTHONPATH}', '${env:PYTHONPATH}'
                    )
                )
            ]
            with io.open(vs_launch_file, "w", encoding="utf-8") as fp:
                fp.write(json.dumps(launch_json, indent=4))

            vs_tasks_file = os.path.join(self.settings_dir, "tasks.json")
            if os.path.exists(vs_tasks_file):
                with io.open(vs_tasks_file, "r", encoding="utf-8") as fp:
                    tasks_json = json.loads(fp.read())
            else:
                tasks_json = dict(version="2.0.0")
            tasks_json.setdefault("tasks", [])
            tasks_json.setdefault("inputs", [])
            tasks_json["tasks"] = [
                t for t in tasks_json["tasks"]
                if t["type"] != "shell" and
                t["name"] != "Plone: Start Test Server"
            ] + [
                ROBOT_SERVER_TASK_TEMPLATE
            ]
            tasks_json["inputs"] = [
                i for i in tasks_json["inputs"]
                if i["id"] != "ploneTestingLayer"
            ] + [
                ROBOT_SERVER_INPUT_TEMPLATE
            ]
            with io.open(vs_tasks_file, "w", encoding="utf-8") as fp:
                fp.write(json.dumps(tasks_json, indent=4))

        return vs_generated_file

    update = install

    def normalize_options(self):
        """This method is simply doing tranformation of cfg string to python datatype.
        For example: yes(cfg) = True(python), 2(cfg) = 2(python)"""

        # Check for required and optional options
        options = self.options.copy()
        # flake8 check
        self._normalize_boolean("flake8-enabled", options)

        # pylint check
        self._normalize_boolean("pylint-enabled", options)

        # jedi check
        self._normalize_boolean("jedi-enabled", options)

        # black check
        self._normalize_boolean("black-enabled", options)

        # isort check
        self._normalize_boolean("isort-enabled", options)

        # mypy check
        self._normalize_boolean("mypy-enabled", options)

        # pep8 check: Issue#1
        self._normalize_boolean("pep8-enabled", options)

        # generate .env file
        self._normalize_boolean("generate-envfile", options)

        # robotframework lsp pythonpath
        self._normalize_boolean("robot-enabled", options)

        # autocomplete
        options["autocomplete-use-omelette"] = self.options[
            "autocomplete-use-omelette"
        ].lower() in ("yes", "y", "true", "t", "on", "1", "sure")

        # Parse linter arguments
        if "pylint-args" in options:
            options["pylint-args"] = self._normalize_linter_args(options["pylint-args"])

        if "flake8-args" in options:
            options["flake8-args"] = self._normalize_linter_args(options["flake8-args"])

        if "black-args" in options:
            options["black-args"] = self._normalize_linter_args(options["black-args"])

        if "isort-args" in options:
            options["isort-args"] = self._normalize_linter_args(options["isort-args"])

        if "mypy-args" in options:
            options["mypy-args"] = self._normalize_linter_args(options["mypy-args"])

        if "pep8-args" in options:
            options["pep8-args"] = self._normalize_linter_args(options["pep8-args"])

        return options

    def _normalize_linter_args(self, args_lines):
        """ """
        args = list()
        for arg_line in args_lines.splitlines():
            if not arg_line or (arg_line and not arg_line.strip()):
                continue
            for arg in arg_line.split(" "):
                if not arg or (arg and not arg.strip()):
                    continue
                args.append(arg)

        return args

    def _normalize_boolean(self, option_name, options):
        """ """
        if option_name in options:
            options[option_name] = options[option_name].lower() in (
                "y",
                "yes",
                "true",
                "t",
                "on",
                "1",
                "sure",
            )

    def _set_defaults(self):
        """This is setting default values of all possible options"""

        self.options.setdefault("project-root", self.buildout["buildout"]["directory"])
        self.options.setdefault("python-path", str(sys.executable))
        if getattr(sys, "real_prefix", None):
            # Python running under virtualenv
            self.options.setdefault(
                "python-virtualenv",
                os.path.dirname(os.path.dirname(self.options["python-path"])),
            )

        self.options.setdefault(
            "omelette-location",
            os.path.join(self.buildout["buildout"]["parts-directory"], "omelette"),
        )

        self.options.setdefault("flake8-enabled", "False")
        self.options.setdefault("flake8-path", "")
        self.options.setdefault("flake8-args", "")
        self.options.setdefault("pylint-enabled", "False")
        self.options.setdefault("pylint-path", "")
        self.options.setdefault("pylint-args", "")
        self.options.setdefault("isort-enabled", "False")
        self.options.setdefault("isort-path", "")
        self.options.setdefault("isort-args", "")
        self.options.setdefault("mypy-enabled", "False")
        self.options.setdefault("mypy-path", "")
        self.options.setdefault("mypy-args", "")
        self.options.setdefault("pep8-enabled", "False")
        self.options.setdefault("pep8-path", "")
        self.options.setdefault("pep8-args", "")
        self.options.setdefault("jedi-enabled", "False")
        self.options.setdefault("black-enabled", "False")
        self.options.setdefault("black-path", "")
        self.options.setdefault("black-args", "")
        self.options.setdefault("formatting-provider", "")
        self.options.setdefault("autocomplete-use-omelette", "False")
        self.options.setdefault("ignore-develop", "False")
        self.options.setdefault("ignores", "")
        self.options.setdefault("packages", "")
        self.options.setdefault("generate-envfile", "True")
        self.options.setdefault("robot-enabled", "False")

    def _prepare_settings(
        self, eggs_locations, develop_eggs_locations, existing_settings
    ):
        """ """
        options = self.normalize_options()
        settings = dict()
        # Base settings
        settings[mappings["python-path"]] = self._resolve_executable_path(
            options["python-path"]
        )

        settings[mappings["autocomplete-extrapaths"]] = eggs_locations

        if options["generate-envfile"]:
            path = os.path.join(self.settings_dir, ".env")
            settings["python.envFile"] = path
            self._write_env_file(eggs_locations, path)

            # Also need terminal.integrated.env.* to make debugging work
            pythonpath = os.pathsep.join(eggs_locations + ["${PYTHONPATH}"])
            settings["terminal.integrated.env.linux"] = dict(PYTHONPATH=pythonpath)
            settings["terminal.integrated.env.osx"] = dict(PYTHONPATH=pythonpath)
            settings["terminal.integrated.env.windows"] = dict(PYTHONPATH=pythonpath)

        if options["autocomplete-use-omelette"]:
            # Add the omelette and the development eggs to the jedi list.
            # This has the advantage of opening files at the omelette location,
            # keeping open files inside the project. Making it possible to
            # navigate to the location in the project, syncing the toolbar, and
            # inspecting the full module not just the individual file.
            settings[mappings["autocomplete-extrapaths"]] = [
                options["omelette-location"]
            ] + develop_eggs_locations

        # Needed for pylance
        settings[mappings["analysis-extrapaths"]] = settings[
            mappings["autocomplete-extrapaths"]
        ]

        # Needed for robotframework-slp
        if "robot-enabled" in self.user_options and options["robot-enabled"]:
            settings[mappings["robot-python-env"]] = dict(PYTHONPATH=pythonpath)

        # Look on Jedi
        if "jedi-enabled" in self.user_options and options["jedi-enabled"]:
            # TODO: not even sure jediEnabled setting is supported anymore
            # settings[mappings["jedi-enabled"]] = options["jedi-enabled"]
            settings[mappings["languageserver"]] = "Jedi"
            # VS code no longer supports this settings
            # settings[mappings["completionsenabled"]] = False
        else:
            # settings[mappings["jedi-enabled"]] = options["jedi-enabled"]
            # TODO: or probably better to remove these settings?
            settings[mappings["languageserver"]] = "Pylance"

        # Setup flake8
        self._sanitize_existing_linter_settings(existing_settings, "flake8", options)
        self._prepare_linter_settings(settings, "flake8", options)

        # Setup pylint
        self._sanitize_existing_linter_settings(existing_settings, "pylint", options)
        self._prepare_linter_settings(settings, "pylint", options)

        # Setup pep8
        self._sanitize_existing_linter_settings(existing_settings, "pep8", options)
        self._prepare_linter_settings(settings, "pep8", options)

        # Setup isort
        self._sanitize_existing_linter_settings(
            existing_settings, "isort", options, allow_key_error=True
        )
        self._prepare_linter_settings(settings, "isort", options, allow_key_error=True)

        # Setup mypy
        self._sanitize_existing_linter_settings(existing_settings, "mypy", options)
        self._prepare_linter_settings(settings, "mypy", options)

        # Setup black, something more that others
        if "black-enabled" in self.user_options and options["black-enabled"]:
            settings[mappings["formatting-provider"]] = "black"
        else:
            if existing_settings.get(mappings["formatting-provider"], None) == "black":
                del existing_settings[mappings["formatting-provider"]]

        self._sanitize_existing_linter_settings(
            existing_settings, "black", options, allow_key_error=True
        )
        self._prepare_linter_settings(settings, "black", options, allow_key_error=True)

        return settings

    def _prepare_linter_settings(self, settings, name, options, allow_key_error=False):
        """All linter related settings are done by this method."""
        linter_enabled = "{name}-enabled".format(name=name)
        linter_path = "{name}-path".format(name=name)
        linter_args = "{name}-args".format(name=name)

        if linter_enabled in self.user_options:
            # we only care if option from user (buildout part)
            try:
                settings[mappings[linter_enabled]] = options[linter_enabled]
            except KeyError:
                if not allow_key_error:
                    raise

        # we care only if linter is active
        linter_executable = options.get(linter_path, "")
        if (
            linter_executable in (None, "")
            and linter_enabled in self.user_options
            and options[linter_enabled]
        ):
            linter_executable = find_executable_path(name)

        if linter_executable:
            settings[mappings[linter_path]] = self._resolve_executable_path(
                linter_executable
            )

        if linter_args in self.user_options and options[linter_args]:
            settings[mappings[linter_args]] = options[linter_args]

    def _write_project_file(self, settings, existing_settings):
        """Project File Writer:
        This method is actual doing writting project file to file system."""
        # Add some python file specific default setting
        for key in python_file_defaults:
            if key not in existing_settings:
                settings[key] = python_file_defaults[key]

        with io.open(
            os.path.join(self.settings_dir, "settings.json"), "w", encoding="utf-8"
        ) as fp:
            try:
                final_settings = existing_settings.copy()
                final_settings.update(settings)
                # sorted by key
                final_settings = OrderedDict(
                    sorted(final_settings.items(), key=lambda t: t[0])
                )
                json_text = json.dumps(final_settings, indent=4, sort_keys=True)
                fp.write(ensure_unicode(json_text))

            except ValueError as exc:
                # catching any json error
                raise UserError(str(exc))

    def _write_env_file(self, eggs_locations, path):
        with io.open(path, "w", encoding="utf-8") as fp:
            paths = os.pathsep.join(eggs_locations)
            path_format = "PYTHONPATH={paths}:${{PYTHONPATH}}"
            fp.write(ensure_unicode(path_format.format(paths=paths)))

    def _resolve_executable_path(self, path_):
        """ """
        # Noramalized Path on demand
        if path_.startswith("~"):
            path_ = os.path.expanduser(path_)

        elif path_.startswith("./"):
            path_ = path_.replace(".", self.buildout["buildout"]["directory"])

        elif path_.startswith("${buildout:directory}"):
            path_ = path_.replace(
                "${buildout:directory}", self.buildout["buildout"]["directory"]
            )

        elif path_.startswith("$project_path/"):
            path_ = path_.replace(
                "$project_path", self.buildout["buildout"]["directory"]
            )

        return path_

    def _sanitize_existing_linter_settings(
        self, existing_settings, name, options, allow_key_error=False
    ):
        """ """
        linter_enabled = "{name}-enabled".format(name=name)
        linter_path = "{name}-path".format(name=name)
        linter_args = "{name}-args".format(name=name)

        if linter_enabled not in self.user_options:
            try:
                key = mappings[linter_enabled]
                if key in existing_settings:
                    del existing_settings[key]
            except KeyError:
                if not allow_key_error:
                    raise

        if linter_path not in self.user_options:
            key = mappings[linter_path]
            if key in existing_settings and not options[linter_enabled]:
                del existing_settings[key]

        if linter_args not in self.user_options:
            key = mappings[linter_args]
            if key in existing_settings:
                del existing_settings[key]


def uninstall(name, options):
    """Nothing much need to do with uninstall, because this recipe is doing so
    much filesystem writting.
    Depends overwrite option, generated project file is removed."""

    logger = logging.getLogger(name)
    logger.info("uninstalling ...")

    project_root = options["project-root"]
    settings_dir = os.path.join(project_root, ".vscode")

    vs_generated_file = os.path.join(settings_dir, "vs-recipe-generated-settings.json")
    if os.path.exists(vs_generated_file):
        os.unlink(vs_generated_file)
        logger.info("removing {0} ...".format(vs_generated_file))
    # xxx: nothing for now, but may be removed what ever in options?
