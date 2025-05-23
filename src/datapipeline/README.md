# Data Load and processing

This module is desined to fetch documents from the Google Cloud Platform (GCP) bucket. After validation, the metadata is loaded into postgres, while the documents are chunked and embedded into the Postgres database.

## **Prerequisites**

### **1. Install Docker**

Make sure Docker is installed on your machine. You can follow [this guide](https://docs.docker.com/get-docker/) to install Docker.

### 2. Enable GPU on docker

```
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg \
  && curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
    sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
    sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt update
sudo apt-get install -y nvidia-container-toolkit
sudo systemctl restart docker.service
docker run --rm --gpus all ubuntu nvidia-smi
```

### 3. Docker login

```
docker login
```

### **4. Clone this repository**

```bash
git clone https://github.com/main/E115_SMART.git
cd E115_SMART
```

### **5. Setup GCP Account**

Go to Google console and ensure that you have access to smart_input_data bucket. Download the key as JSON file and rename it `smart_input_key.json`. Your folder structure should look like:

```
|-E115_SMART
|-secrets
  |-smart_input_key.json
```

## **Organization**

```
├── Readme.md
├── sql
│   └── init.sql
└── src
    ├── datapipeline
    |   ├── __init__.py
    │   ├── Advanced_semantic_chunker.py
    │   ├── datapipeline.py
    │   ├── docker_entrypoint.sh
    │   ├── Dockerfile
    │   ├── Pipfile
    │   ├── Pipfile.lock
    │   └── README.md
    ├── docker-compose.yml
    ├── docker-shell.sh
    └── Dockerfile.postgres
```

## **Running the system**

### **Run the image datapipeline to prepare the input data in /src**

- **Loads** documents from GCP
- **Validates** to ensure that data is complete
- **Chunks** text documents using semantic chunking. Change default to recursive/semantic in main to change the chunking method. Current default = recursive.
- **Embeds** the text into a vector space
- **Stores** embeddings in a **PostgreSQL + pgvector database**

```bash
sh docker-shell.sh datapipeline
```

## To access the database container

### Ubuntu

```
sudo apt update && sudo apt install -y postgresql-client
psql -U postgres -h localhost
```

### OS independent

```
docker exec -it postgres /bin/bash
psql -U postgres
\c smart
```

## For clean reruns

```
docker-compose stop
docker system prune
sudo rm -rf ../persistent-folder/
```
