
from setuptools import setup

def readme():
    with open('README.md') as f:
        return f.read()

setup(
    name='ecomp',
    version='0.1.0',
    description='etcd-compute virtualization explorer',
    long_description=readme(),
    url='https://github.com/cdent/etcd-compute',
    author='Chris Dent',
    author_email='cdent@anticdent.org',
    license='Apache2',
    packages=['ecomp'],
    install_requires=[
        'etcd3',
        'requests',
        'PyYAML',
        "libvirt-python; sys_platform != 'darwin'",
        'cachecontrol[filecache]',
        'bottle',
        'psutil',
    ],
    entry_points = {
        'console_scripts': [
            'eschedule=ecomp.schedule:run',
            'ecompute=ecomp.compute:run',
        ],
    }
)
