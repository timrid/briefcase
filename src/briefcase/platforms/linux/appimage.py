from __future__ import annotations

import os
import subprocess

from briefcase.commands import (
    BuildCommand,
    CreateCommand,
    PackageCommand,
    PublishCommand,
    RunCommand,
    UpdateCommand,
)
from briefcase.config import AppConfig
from briefcase.exceptions import (
    BriefcaseCommandError,
    BriefcaseConfigError,
    UnsupportedHostError,
)
from briefcase.integrations.docker import Docker, DockerAppContext
from briefcase.integrations.linuxdeploy import LinuxDeploy
from briefcase.integrations.subprocess import NativeAppContext
from briefcase.platforms.linux import (
    DockerOpenCommand,
    LinuxMixin,
    LocalRequirementsMixin,
)


class LinuxAppImagePassiveMixin(LinuxMixin):
    # The Passive mixin honors the docker options, but doesn't try to verify
    # docker exists. It is used by commands that are "passive" from the
    # perspective of the build system, like open and run.
    output_format = "appimage"
    supported_host_os = {"Darwin", "Linux"}
    supported_host_os_reason = (
        "Linux AppImages can only be built on Linux, or on macOS using Docker."
    )
    platform_target_version = "0.3.20"

    def appdir_path(self, app):
        return self.bundle_path(app) / f"{app.formal_name}.AppDir"

    def project_path(self, app):
        return self.bundle_path(app)

    def binary_name(self, app):
        safe_name = app.formal_name.replace(" ", "_")
        arch = LinuxDeploy.arch(self.tools)
        return f"{safe_name}-{app.version}-{arch}.AppImage"

    def binary_path(self, app):
        return self.bundle_path(app) / self.binary_name(app)

    def distribution_path(self, app):
        return self.dist_path / self.binary_name(app)

    def verify_tools(self):
        """Verify the AppImage LinuxDeploy tool and its plugins exist."""
        super().verify_tools()
        LinuxDeploy.verify(tools=self.tools)

    def add_options(self, parser):
        super().add_options(parser)
        parser.add_argument(
            "--no-docker",
            dest="use_docker",
            action="store_false",
            help="Don't use Docker for building the AppImage",
            required=False,
        )
        parser.add_argument(
            "--Xdocker-build",
            action="append",
            dest="extra_docker_build_args",
            help="Additional arguments to use when building the Docker image",
            required=False,
        )

    def parse_options(self, extra):
        """Extract the use_docker option."""
        options, overrides = super().parse_options(extra)
        self.use_docker = options.pop("use_docker")
        self.extra_docker_build_args = options.pop("extra_docker_build_args")
        return options, overrides

    def clone_options(self, command):
        """Clone the use_docker option."""
        super().clone_options(command)
        self.use_docker = command.use_docker
        self.extra_docker_build_args = command.extra_docker_build_args

    def finalize_app_config(self, app: AppConfig):
        """If we're *not* using Docker, warn the user about portability."""
        if not self.use_docker:
            self.console.warning(
                """\
*************************************************************************
** WARNING: Building a Local AppImage!                                 **
*************************************************************************

    You are building an AppImage outside Docker. The resulting AppImage
    will work, but will not be as portable as a Docker-based AppImage.
    Any `manylinux` setting will be ignored.

*************************************************************************
"""
            )

        self.console.warning(
            """\
*************************************************************************
** WARNING: Use of AppImage is not recommended!                        **
*************************************************************************

    Briefcase supports AppImage in a best-effort capacity. It has proven
    to be highly unreliable as a distribution platform. AppImages cannot
    use pre-compiled binary wheels, and has significant problems with
    most commonly used GUI toolkits (including GTK and PySide).

    Consider using system packages or Flatpak for Linux app
    distribution.

*************************************************************************
"""
        )


class LinuxAppImageMostlyPassiveMixin(LinuxAppImagePassiveMixin):
    # The Mostly Passive mixin verifies that Docker exists and can be run, but
    # doesn't require that we're actually in a Linux environment.
    def docker_image_tag(self, app):
        """The Docker image tag for an app."""
        try:
            return f"briefcase/{app.bundle_identifier.lower()}:{app.manylinux}-appimage"
        except AttributeError:
            return f"briefcase/{app.bundle_identifier.lower()}:appimage"

    def verify_tools(self):
        """If we're using docker, verify that it is available."""
        super().verify_tools()
        if self.use_docker:
            Docker.verify(tools=self.tools)

    def verify_app_tools(self, app: AppConfig):
        """Verify App environment is prepared and available.

        When Docker is used, create or update a Docker image for the App. Without
        Docker, the host machine will be used as the App environment.

        :param app: The application being built
        """
        if self.use_docker:
            DockerAppContext.verify(
                tools=self.tools,
                app=app,
                image_tag=self.docker_image_tag(app),
                dockerfile_path=self.bundle_path(app) / "Dockerfile",
                app_base_path=self.base_path,
                host_bundle_path=self.bundle_path(app),
                host_data_path=self.data_path,
                python_version=self.python_version_tag,
                extra_build_args=self.extra_docker_build_args,
            )
        else:
            NativeAppContext.verify(tools=self.tools, app=app)

        # Establish Docker as app context before letting super set subprocess
        super().verify_app_tools(app)


class LinuxAppImageMixin(LinuxAppImageMostlyPassiveMixin):
    def verify_host(self):
        """If we're *not* using Docker, verify that we're actually on Linux."""
        super().verify_host()
        if not self.use_docker and self.tools.host_os != "Linux":
            raise UnsupportedHostError(self.supported_host_os_reason)


class LinuxAppImageCreateCommand(
    LinuxAppImageMixin,
    LocalRequirementsMixin,
    CreateCommand,
):
    description = "Create and populate a Linux AppImage."

    def output_format_template_context(self, app: AppConfig):
        context = super().output_format_template_context(app)

        try:
            manylinux_arch = {
                "x86_64": "x86_64",
                "i386": "i686",
                "aarch64": "aarch64",
            }[LinuxDeploy.arch(self.tools)]
        except KeyError:
            manylinux_arch = LinuxDeploy.arch(self.tools)
            self.console.warning(
                f"There is no manylinux base image for {manylinux_arch}"
            )

        # Add the manylinux tag to the template context.
        try:
            tag = getattr(app, "manylinux_image_tag", "latest")
            context["manylinux_image"] = f"{app.manylinux}_{manylinux_arch}:{tag}"
            if app.manylinux in {"manylinux1", "manylinux2010", "manylinux2014"}:
                context["vendor_base"] = "centos"
            elif app.manylinux == "manylinux_2_24":
                context["vendor_base"] = "debian"
            elif app.manylinux.startswith("manylinux_2_"):
                context["vendor_base"] = "almalinux"
            else:
                raise BriefcaseConfigError(f"Unknown manylinux tag {app.manylinux!r}")
        except AttributeError:
            pass

        # Use the non-root user if Docker is not mapping usernames
        try:
            context["use_non_root_user"] = not self.tools.docker.is_user_mapped
        except AttributeError:
            pass  # ignore if not using Docker

        return context

    def _cleanup_app_support_package(self, support_path):
        # On Windows, the support path is co-mingled with app content.
        # This means updating the support package is imperfect.
        # Warn the user that there could be problems.
        self.console.warning(
            """
*************************************************************************
** WARNING: Support package update may be imperfect                    **
*************************************************************************

    Support packages in Linux AppImages are overlaid with app content,
    so it isn't possible to remove all old support files before
    installing new ones.

    Briefcase will unpack the new support package without cleaning up
    existing support package content. This *should* work; however,
    ensure a reproducible release artefacts, it is advisable to
    perform a clean app build before release.

*************************************************************************
"""
        )


class LinuxAppImageUpdateCommand(LinuxAppImageCreateCommand, UpdateCommand):
    description = "Update an existing Linux AppImage."


class LinuxAppImageOpenCommand(LinuxAppImageMostlyPassiveMixin, DockerOpenCommand):
    description = (
        "Open a shell in a Docker container for an existing Linux AppImage project."
    )


class LinuxAppImageBuildCommand(LinuxAppImageMixin, BuildCommand):
    description = "Build a Linux AppImage."

    def build_app(self, app: AppConfig, **kwargs):  # pragma: no-cover-if-is-windows
        """Build an application.

        :param app: The application to build
        """
        # Build a dictionary of environment definitions that are required
        env = {}

        self.console.info("Checking for Linuxdeploy plugins...", prefix=app.app_name)
        try:
            plugins = self.tools.linuxdeploy.verify_plugins(
                app.linuxdeploy_plugins,
                bundle_path=self.bundle_path(app),
            )

            self.console.info("Configuring Linuxdeploy plugins...", prefix=app.app_name)
            # We need to add the location of the linuxdeploy plugins to the PATH.
            # However, if we are running inside Docker, we need to know the
            # environment *inside* the Docker container.
            echo_cmd = ["/bin/sh", "-c", "echo $PATH"]
            base_path = self.tools[app].app_context.check_output(echo_cmd).strip()

            # Add any plugin-required environment variables
            for plugin in plugins.values():
                env.update(plugin.env)

            # Construct a path that has been prepended with the path to the plugins
            env["PATH"] = os.pathsep.join(
                [os.fsdecode(plugin.file_path) for plugin in plugins.values()]
                + [base_path]
            )
        except AttributeError:
            self.console.info("No linuxdeploy plugins configured.")
            plugins = {}

        self.console.info("Building AppImage...", prefix=app.app_name)
        with self.console.wait_bar("Building..."):
            try:
                # For some reason, the version has to be passed in as an
                # environment variable, *not* in the configuration.
                env["LINUXDEPLOY_OUTPUT_VERSION"] = app.version
                # The internals of the binary aren't inherently visible, so
                # there's no need to package copyright files. These files
                # appear to be missing by default in the OS dev packages anyway,
                # so this effectively silences a bunch of warnings that can't
                # be easily resolved by the end user.
                env["DISABLE_COPYRIGHT_FILES_DEPLOYMENT"] = "1"
                # AppImages do not run natively within a Docker container. This
                # treats the AppImage like a self-extracting executable. Using
                # this environment variable instead of --appimage-extract-and-run
                # is necessary to ensure AppImage plugins are extracted as well.
                env["APPIMAGE_EXTRACT_AND_RUN"] = "1"
                # Explicitly declare target architecture as the current architecture.
                # This can be used by some linuxdeploy plugins.
                env["ARCH"] = self.tools.host_arch

                # Enable debug logging for linuxdeploy GTK and Qt plugins
                if self.console.is_deep_debug:
                    env["DEBUG"] = "1"

                # Find all the .so files in app and app_packages,
                # so they can be passed in to linuxdeploy to have their
                # requirements added to the AppImage. Looks for any .so file
                # in the application, and make sure it is marked for deployment.
                so_folders = {
                    so_file.parent for so_file in self.appdir_path(app).glob("**/*.so")
                }

                additional_args = []
                for folder in sorted(so_folders):
                    additional_args.extend(["--deploy-deps-only", str(folder)])

                for plugin in plugins:
                    additional_args.extend(["--plugin", plugin])

                # Build the AppImage.
                self.tools[app].app_context.run(
                    [
                        self.tools.linuxdeploy.file_path
                        / self.tools.linuxdeploy.file_name,
                        "--appdir",
                        self.appdir_path(app),
                        "--desktop-file",
                        self.appdir_path(app) / f"{app.bundle_identifier}.desktop",
                        "--output",
                        "appimage",
                        "-v0" if self.console.is_deep_debug else "-v1",
                    ]
                    + additional_args,
                    env=env,
                    check=True,
                    cwd=self.bundle_path(app),
                )

                # Make the binary executable.
                self.tools.os.chmod(self.binary_path(app), 0o755)
            except subprocess.CalledProcessError as e:
                raise BriefcaseCommandError(
                    f"Error while building app {app.app_name}."
                ) from e


class LinuxAppImageRunCommand(LinuxAppImagePassiveMixin, RunCommand):
    description = "Run a Linux AppImage."
    supported_host_os = {"Linux"}
    supported_host_os_reason = "Linux AppImages can only be executed on Linux."

    def run_app(
        self,
        app: AppConfig,
        passthrough: list[str],
        **kwargs,
    ):
        """Start the application.

        :param app: The config object for the app
        :param passthrough: The list of arguments to pass to the app
        """
        # Set up the log stream
        kwargs = self._prepare_app_kwargs(app=app)

        # Console apps must operate in non-streaming mode so that console input can
        # be handled correctly. However, if we're in test mode, we *must* stream so
        # that we can see the test exit sentinel
        if app.console_app and not app.test_mode:
            self.console.info("=" * 75)
            self.tools.subprocess.run(
                [self.binary_path(app)] + passthrough,
                cwd=self.tools.home_path,
                bufsize=1,
                stream_output=False,
                **kwargs,
            )
        else:
            # Start the app in a way that lets us stream the logs
            app_popen = self.tools.subprocess.Popen(
                [self.binary_path(app)] + passthrough,
                cwd=self.tools.home_path,
                **kwargs,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
            )

            # Start streaming logs for the app.
            self._stream_app_logs(
                app,
                popen=app_popen,
                clean_output=False,
            )


class LinuxAppImagePackageCommand(LinuxAppImageMixin, PackageCommand):
    description = "Package a Linux AppImage."

    def package_app(self, app: AppConfig, **kwargs):
        """Package an AppImage.

        :param app: The application to package
        """
        self.tools.shutil.copy(self.binary_path(app), self.distribution_path(app))


class LinuxAppImagePublishCommand(LinuxAppImageMixin, PublishCommand):
    description = "Publish a Linux AppImage."


# Declare the briefcase command bindings
create = LinuxAppImageCreateCommand
update = LinuxAppImageUpdateCommand
open = LinuxAppImageOpenCommand
build = LinuxAppImageBuildCommand
run = LinuxAppImageRunCommand
package = LinuxAppImagePackageCommand
publish = LinuxAppImagePublishCommand
