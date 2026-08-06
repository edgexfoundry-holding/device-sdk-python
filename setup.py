#!/usr/bin/env python
# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

with open("requirements.txt", "r", encoding="utf-8") as fh:
    requirements = [line.strip() for line in fh if line.strip() and not line.startswith("#")]

setup(
    name="device-sdk-py",
    version="4.0.0",
    author="YIQISOFT",
    author_email="",
    description="EdgeX Foundry Device Service SDK for Python",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/edgexfoundry/device-sdk-python",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    classifiers=[
        "License :: OSI Approved :: Apache Software License",
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
    python_requires=">=3.10",
    install_requires=requirements,
    extras_require={
        "dev": [
            "pytest>=7.0",
            "coverage>=7.0",
        ],
    },
)