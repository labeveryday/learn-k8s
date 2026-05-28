# Lab 04 — Docker Networking

## 1. Default `bridge` network

By default, containers join the `bridge` network — a Linux bridge with NAT to the host.

```bash
docker network ls
docker network inspect bridge | jq '.[0].Containers'
```

Each container gets a private IP (e.g., `172.17.0.x`). `-p 8080:80` adds a NAT rule on the host: any traffic to host:8080 → container:80.

## 2. Custom networks (the right way for multi-container apps)

The default bridge has *no* automatic DNS between containers. **Always create a user-defined network** for an app:

```bash
docker network create demo
docker run -d --network demo --name api nginx:1.27-alpine
docker run --rm --network demo curlimages/curl:latest curl -s http://api/
```

In a user-defined network, `--name` becomes a DNS name. This is the seed of how K8s Services work.

## 3. Network modes

| Mode | Behavior |
|------|----------|
| `bridge` (default) | private subnet + NAT |
| `host` | share the host's network namespace (Linux only; on Mac, "host" = the VM) |
| `none` | no network |
| `container:<name>` | share another container's `net` namespace (this is how K8s Pods work!) |

Pod-style sharing demo:

```bash
docker run -d --name shared --network bridge nginx:1.27-alpine
docker run --rm -it --network container:shared curlimages/curl:latest sh -c 'wget -qO- localhost'
# Both containers share the same net ns — `localhost` reaches nginx.
```

This is **exactly** how a Pod works in Kubernetes.

## 4. Port publishing nuances

```bash
-p 8080:80              # host:container
-p 127.0.0.1:8080:80    # bind to loopback only (more secure)
-p 8080:80/udp          # UDP
-P                      # publish all EXPOSEd ports to random host ports
docker port <name>      # show mappings
```

## 5. Practice

1. Create a `demo` network. Run `redis` and a debug container that pings it by name.
2. Run two `nginx`es; share the second's network with `--network container:nginx1`. Which one answers `curl localhost`? Why?
3. Inspect the bridge: `ip link` on the host (or in Docker Desktop's VM via `docker run -it --rm --privileged --pid=host justincormack/nsenter1 ip link`). Find the `docker0` bridge.
4. Read `iptables -t nat -L DOCKER -n` (in the VM) — see the NAT rules `-p` creates.
