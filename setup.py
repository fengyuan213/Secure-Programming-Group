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
        "websockets==12.0",
    ],
    python_requires=">=3.8",
    entry_points={
        'console_scripts': [
            'socp-server=server.server:main',
            'socp-client=client.client:main',
        ],
    },
)
