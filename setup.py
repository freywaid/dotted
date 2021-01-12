import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

with open('LICENSE', 'r') as f:
    license = f.read()


setuptools.setup(
    name="dotted-notation",
    version="0.4.0",
    author="Frey Waid",
    author_email="logophage1@gmail.com",
    description="Dotted notation parser with pattern matching",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/freywaid/dotted",
    packages=setuptools.find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    license=license,
    python_requires='>=3.6',
    install_requires=['pyparsing>=2.4,<3',],
)
