import setuptools

with open("README.md", "rt") as f:
    long_description = f.read()

with open('LICENSE', 'rt') as f:
    license = f.read()


setuptools.setup(
    name="dotted-notation",
    version="0.4.2",
    author="Frey Waid",
    author_email="logophage1@gmail.com",
    description="Dotted notation parser with pattern matching",
    license=license,
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/freywaid/dotted",
    packages=setuptools.find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires='>=3.6',
    install_requires=['pyparsing>=2.4,<3',],
)
