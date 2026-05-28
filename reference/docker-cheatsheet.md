# Docker Cheatsheet

## Run / lifecycle
```
docker run --rm -it IMG bash
docker run -d --name N -p 8080:80 IMG
docker ps                          running
docker ps -a                       all
docker logs -f N
docker exec -it N sh
docker stop N && docker rm N
docker inspect N | jq .
docker stats                       live cpu/mem
```

## Build
```
docker build -t name:tag .
docker build --no-cache -t n:t .
docker build --target=stage -t n:t .   multi-stage
```

## Images
```
docker images
docker image inspect IMG
docker history IMG
docker pull IMG
docker push REPO/NAME:TAG
docker tag SRC DST
docker save IMG -o f.tar
docker load -i f.tar
docker image prune -a
```

## Volumes
```
docker volume create v
docker volume ls
docker volume inspect v
docker run -v v:/data IMG          named volume
docker run -v /host/p:/container/p IMG   bind mount
docker run --tmpfs /scratch IMG    in-memory
```

## Networks
```
docker network ls
docker network create demo
docker run --network demo --name a IMG
docker run --network container:a IMG    share net ns
docker network inspect demo
```

## Compose
```
docker compose up -d
docker compose down [-v]
docker compose ps
docker compose logs -f svc
docker compose exec svc sh
docker compose build [svc]
docker compose restart svc
```

## Cleanup
```
docker system df
docker system prune                dangling
docker system prune -a --volumes   nuke
```

## Useful one-liners
```
docker inspect N | jq -r '.[0].NetworkSettings.IPAddress'
docker exec N cat /proc/1/cmdline | tr '\0' ' '
docker run --rm -it --pid=container:N --net=container:N nicolaka/netshoot
```
