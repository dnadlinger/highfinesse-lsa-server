from setuptools import find_packages, setup

setup(
    name='highfinesse-lsa-server',
    version='0.0.1',
    url='https://github.com/klickverbot/highfinesse-lsa-server',
    author='David P. Nadlinger',
    packages=['highfinesse_lsa'],
    entry_points={
        'console_scripts': ['highfinesse_lsa_server=highfinesse_lsa.server:main']
    },
    install_requires=[
        'artiq',
        'llama'
    ]
)
