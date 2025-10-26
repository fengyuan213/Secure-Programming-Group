#!/usr/bin/env python3
"""
Setup script for SOCP v1.3 Implementation
"""

from setuptools import setup, find_packages

setup(
    name="socp",
    version="0.0.1",
    description="Secure Overlay Chat Protocol implementation",
    packages=find_packages(),
    install_requires=[
        "websockets==15.0",
        "cryptography==43.0.1",
        "click==8.1.7",
        "typer==0.12.3",
        "rich==13.9.2",
        "aioconsole==0.8.1",
        "tqdm==4.66.5",
        "PyYAML==6.0.1",
    ],
    extras_require={
        "test": [
            "pytest==8.4.2",
            "pytest-asyncio==1.2.0",
        ],
    },
    python_requires=">=3.8",
    entry_points={
        'console_scripts': [
            'socp-server=server.server:main',
            'socp-client=client.socp_cli:main',
        ],
    },
)
