# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
root = Path(SPECPATH)
analysis = Analysis([str(root/'ge_wine_macos.py')], pathex=[str(root)],
                    datas=[(str(root/'macos_runtime.json'),'.'),(str(root/'README-macos.md'),'documentation')], hiddenimports=['tkinter','psutil','certifi'],
                    excludes=['ge_memory','pytest','unittest'])
archive = PYZ(analysis.pure)
exe = EXE(archive, analysis.scripts, [], exclude_binaries=True, name='GE-Wine', console=True,
          argv_emulation=False, target_arch=None)
collection = COLLECT(exe,analysis.binaries,analysis.datas,name='GE-Wine',strip=False,upx=False)
app = BUNDLE(collection,name='GE-Wine.app',bundle_identifier='org.ge-wine.macos',
             info_plist={'CFBundleShortVersionString':'0.13.2','CFBundleVersion':'0.13.2',
                         'LSMinimumSystemVersion':'13.0','NSHighResolutionCapable':True,
                         'CFBundleDisplayName':'GE-Wine',
                         'CFBundleDocumentTypes':[{'CFBundleTypeName':'Windows program','CFBundleTypeRole':'Viewer',
                                                   'CFBundleTypeExtensions':['exe','msi']} ]})
