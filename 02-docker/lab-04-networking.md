# Lab 04: Docker Networking

**What you'll build:** not an app, but a mental model of how containers reach each other and the
outside world. You'll attach containers to the default `bridge`, build a user-defined network
where containers resolve each other by name (DNS), and finally make two containers share
one network namespace so `localhost` reaches across them. That last move isn't a Docker trick:
it is how a Kubernetes Pod works. By the end you can predict which network mode gives
you DNS, which gives you a shared `localhost`, and where the NAT rules that make `-p` work
live.

> **The one idea:** a container's network is a namespace (Phase 1 Lab 04)
> plus wiring: a bridge to a subnet, NAT to the host, and optionally a name in DNS. Every section
> below is one piece of that wiring. When you get to Pods, the trick is that several containers
> are handed the same network namespace, so they're already on `localhost` with each other.

## 0. Two primitives you need first

Phase 1 Lab 03 covered loopback, ports, sockets, DNS, and private subnets but not these:

- **Linux bridge**: a virtual switch in software. Docker attaches each container's network interface to it so they can talk on one private subnet.
- **NAT** (Network Address Translation): rewriting packet addresses so containers on a private subnet (`172.17.0.x`) can reach the outside, and a host port can be forwarded to a container. `-p host:container` creates a NAT rule.

Hold onto the distinction: the **bridge** is what lets containers talk to each other on a subnet;
**NAT** is what lets that subnet talk to and from the host and internet. The rest of the lab
varies those two.

## 1. Default `bridge` network

By default, containers join the `bridge` network, a Linux bridge with NAT to the host. You didn't
ask for it; it's the fallback every `docker run` without `--network` lands on.

```bash
docker network ls                                      # list networks: bridge, host, none always exist
docker network inspect bridge | jq '.[0].Containers'   # currently-attached containers + their IPs; {} or null if none are running
```

- `network ls` always shows three built-ins (`bridge`, `host`, `none`), which map to the modes in section 3.
- `inspect` dumps the full network as a JSON array (one element); `jq '.[0].Containers'` pulls
  the attached-containers map so you don't drown in the full config.

**What you should see:** `network ls` lists at least `bridge`/`host`/`none`. The `jq` output is a
map of container ID → its IP on this subnet (or `{}`/`null` if nothing is running on `bridge` right
now). Those IPs are the addresses containers use to reach each other here.

Each container gets a private IP (e.g., `172.17.0.x`). `-p 8080:80` adds a NAT rule on the host: any traffic to host:8080 → container:80.

## 2. Custom networks (the right way for multi-container apps)

The default bridge has no automatic DNS between containers. **Always create a user-defined network** for an app:

```bash
docker network create demo                                                     # one user-defined bridge with built-in DNS
docker run -d --network demo --name api nginx:1.27-alpine                       # 'api' becomes a resolvable DNS name on 'demo'
docker run --rm --network demo curlimages/curl:latest curl -s http://api/       # works: returns nginx HTML

# Same thing on the DEFAULT bridge fails to resolve the name:
docker run -d --name api2 nginx:1.27-alpine                                      # on default bridge - name is NOT a DNS entry
docker run --rm curlimages/curl:latest curl -s http://api2/                      # error: Could not resolve host: api2
docker rm -f api2                                                                # clean up the demo's failed half
```

- `--network demo` puts the container on the user-defined network you made, the one with DNS.
- `-d` runs `api` detached (background) so it keeps serving; `--rm` makes the throwaway curl client
  delete itself the instant it exits.
- `--name api` is the load-bearing part: on a **user-defined** network the name is registered in
  Docker's embedded DNS, so `http://api/` resolves. The `api2` run does the same `--name` but on the
  **default** bridge, where names are not resolved. That's the whole point of the contrast.
- `rm -f api2` force-removes the still-running container (the `-f` lets you delete it without stopping first).

(`curlimages/curl` is a tiny image that contains `curl` and nothing else, a throwaway client, since the `nginx` alpine image has no `curl` of its own.)

**What you should see:** the first curl returns nginx's default HTML; the second fails with
`Could not resolve host: api2`. Same image, same `--name`; the only difference is the network.
That single difference is the seed of how K8s Services work: in Kubernetes you reach a workload by
**name**, and that only works because something is running DNS for the network.

## 3. Network modes

Every `docker run` picks one of these. They're the same three built-ins from section 1, plus the
"borrow another container's namespace" mode that matters most for Kubernetes:

| Mode | Behavior |
|------|----------|
| `bridge` (default) | private subnet + NAT |
| `host` | share the host's network namespace (Linux only; on Mac, "host" = the VM) |
| `none` | no network |
| `container:<name>` | share another container's `net` namespace (this is how K8s Pods work!) |

- `host` means the container has no network namespace of its own; it sees the host's interfaces
  and ports directly. Fast, but no isolation and `-p` becomes meaningless (the port is the host's).
- `none` gives the container a network namespace with only loopback, useful for jobs that must not
  touch the network.
- `container:<name>` is the one to internalize: the second container reuses the **first's** network
  namespace, so they share one IP, one set of ports, and one `localhost`.

Pod-style sharing demo:

```bash
docker run -d --name shared --network bridge nginx:1.27-alpine                  # the 'owner' of the net namespace
docker run --rm -it --network container:shared curlimages/curl:latest sh -c 'wget -qO- localhost'   # joins shared's net ns
# Both containers share the same net ns - `localhost` reaches nginx.
```

- `--network container:shared` tells the curl container not to make its own network namespace but
  to join `shared`'s. Now both are on the same `localhost`.
- `wget -qO- localhost` fetches `http://localhost/` and prints it (`-q` quiet, `-O-` to stdout). It
  hits nginx even though nginx runs in the other container, because there's only one `localhost`
  between them.

**What you should see:** the nginx welcome HTML, fetched over `localhost` from a container that isn't
running nginx. That shared-`localhost` behavior is the entire premise of a Pod.

This is how a Pod works in Kubernetes. A Pod is a group of containers handed one shared
network namespace (Kubernetes injects a tiny "pause" container to own it, then every app container
joins with `container:`-style sharing), which is why containers in a Pod reach each other over
`localhost` and share one Pod IP.

## 4. Port publishing nuances

`-p` (and `-P`) is how a container's port becomes reachable from outside its subnet. Each variant
writes a different NAT rule:

```bash
-p 8080:80              # host:container - host:8080 → container:80 (the common case)
-p 127.0.0.1:8080:80    # bind to loopback only (more secure) - only THIS host can reach it, not the LAN
-p 8080:80/udp          # UDP instead of the default TCP
-P                      # publish all EXPOSEd ports to random host ports
docker port <name>      # show the actual host→container mappings (essential after -P picks random ports)
```

- `-p 8080:80` is `host:container`: traffic to the host's `8080` is NAT'd to the container's `80`.
- Prefixing a host IP (`127.0.0.1:8080:80`) restricts which host interface accepts it: loopback
  only means nothing off the machine can hit it. Omitting the IP binds all interfaces (LAN-reachable).
- `-P` (capital) auto-publishes every port the image `EXPOSE`d to random high host ports. Handy,
  but you then need `docker port` to discover what they landed on.

**What you should see:** `docker port <name>` prints lines like `80/tcp -> 0.0.0.0:8080`, the live
NAT mappings for that container. Those are the same rules you'll go read raw in Practice #4.

## 5. Practice

1. Create a `demo` network. Run `redis` and a debug container that pings it by name.
2. Run two `nginx`es; share the second's network with `--network container:nginx1`. Which one answers `curl localhost`? Why?
3. Inspect the bridge: `ip link` on the host (or in Docker Desktop's VM via `docker run -it --rm --privileged --pid=host justincormack/nsenter1 ip link`). Find the `docker0` bridge, the default bridge device Docker creates. (`justincormack/nsenter1` is a helper image that uses `--pid=host` + `nsenter` from Phase 1 Lab 04 to drop you into the Colima/Desktop VM's host namespaces, so you can see `docker0`, which lives in that VM, not on your Mac.)
4. Read `iptables -t nat -L DOCKER -n` (in the VM). `iptables` is the Linux packet-filter/NAT tool; Docker auto-generates the `DOCKER` chain to hold the NAT rules `-p` creates.

## Next

→ `lab-05-volumes.md`: a container's writable layer dies with the container. A **volume**
keeps data alive across `docker rm`, the same decoupling a Kubernetes `PersistentVolumeClaim`
gives a Pod (Phase 03 lab-06).
