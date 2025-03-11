# e115_SMART
Repo for project for E115

## Enable GPU on docker
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

## Docker login
```
docker login
``` 

## Access the database
### Ubuntu
```
sudo apt update && sudo apt install -y postgresql-client
psql -U postgres -h localhost
```
### OS independent
```
docker exec -it postgres /bin/bash
psql -U postgres
```
