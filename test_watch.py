import os
import sys
import time
import tempfile

_HERE = os.path.dirname(os.path.abspath('.'))
_SRC = os.path.abspath(os.path.join(_HERE, 'src'))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from device_sdk_py.service.bootstrap import bootstrap

class _Driver:
    def start(self): pass

def _make_service(config=None):
    return bootstrap('device-simple', '0.0.0', _Driver(), configuration=config)

class _MockConfig:
    def __init__(self):
        self.custom_config_path = None

temp_dir = tempfile.mkdtemp()
config_file = os.path.join(temp_dir, 'custom.yaml')
with open(config_file, 'w') as f:
    f.write('setting1: value1\n')

config = _MockConfig()
config.custom_config_path = config_file

ds = _make_service()
ds.load_custom_config(config, 'test-section')

called = []
def callback(new_config):
    print('Callback called with:', new_config)
    called.append(new_config)

ds.listen_for_custom_config_changes(config, 'test-section', callback)

time.sleep(0.2)
print('Writing...')
with open(config_file, 'w') as f:
    f.write('setting1: changed\n')

time.sleep(1.0)
print('Called:', len(called), called)

ds._shutdown()
import shutil
shutil.rmtree(temp_dir, ignore_errors=True)