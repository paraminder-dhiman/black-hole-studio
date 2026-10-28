# Black Hole Studio - physicall bsaed black holes for Blender.
# SPDX-License-Identifier: GPL-3.0-or-later

try:
    import bpy  # noqa: F401
except ImportError:      # imported by the test-suite outside Blender
    bpy = None

if bpy is not None:
    from . import properties, operators, ui

    _modules = (properties, operators, ui)

    def register():
        for module in _modules:
            module.register()

    def unregister():
        for module in reversed(_modules):
            module.unregister()