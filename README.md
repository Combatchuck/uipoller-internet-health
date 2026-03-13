# UniFi Network Monitoring Stack

Docker Compose stack for monitoring UniFi networks with UnPoller, Prometheus, and Grafana.

## Quick Start

1. **Clone and configure**

   ```bash
   git clone git@github.com:timothystewart6/unpoller-unifi.git
   cd unpoller-unifi
   ```

   > **Important:** the compose files are written to mount host paths under `.../unpoller-unifi` (e.g., `/mnt/user/Docker_Mounts/unpoller-unifi`).
   > If you rename or relocate the folder, update the volume paths in `compose.yaml` (or the compose file you use) accordingly.

2. **Edit UniFi credentials**

   Update `unpoller/.env` with your controller details, it's advised to create a dedicated local UniFi account that has read only access to your Network controller:

   ```env
    UP_UniFi_CONTROLLER_0_URL=https://192.168.10.1
    UP_UniFi_CONTROLLER_0_USER=unpoller
    UP_UniFi_CONTROLLER_0_PASS=password123
    UP_UniFi_CONTROLLER_0_SITE=default
   ```

   Update Grafana admin credentials in `grafana/.env`:

   ```env
   GF_SECURITY_ADMIN_USER=admin
   GF_SECURITY_ADMIN_PASSWORD=admin123
   ```

   Optionally update timezone in `dozzle/.env`, `grafana/.env`, `prometheus/.env`, `unpoller/.env`:

   ```env
   TZ=Your/Timezone
   ```

3. **Start the stack**

   ```bash
   docker-compose up -d
   ```

## Access

| Service | URL |
| ------- | --- |
| Grafana | <http://localhost:3000> |
| Prometheus | <http://localhost:9090> |
| Dozzle (logs) | <http://localhost:8080> |

**Grafana default login**: admin/admin123 (change in `grafana/.env`)

## What's Included

- Pre-configured environment files for all services
- Grafana dashboards for UniFi devices (Access Points, Switches, Gateway, Clients, DPI, Sites, PDU)
- **Prometheus data source auto-provisioned** (named `DS_PROMETHEUS` so dashboards using the `${DS_PROMETHEUS}` variable work correctly)
- Log viewer with Dozzle

> ⚡ **Added metrics for internet health** – you can now deploy a Blackbox exporter to probe external endpoints (ICMP, HTTP, DNS, etc.) and optionally a speedtest exporter for bandwidth/latency measurements.  See the [Configuration](#configuration) section for details.

## Resources

- **📖 Detailed Guide**: [UniFi Observability with UnPoller, Prometheus, and Grafana](https://technotim.com/posts/unpoller-unifi-metrics/)
- **🎥 Video Tutorial**: [UniFi Observability Done Right (Unpoller + Grafana Walkthrough)](https://www.youtube.com/watch?v=cVCPKTHEnI8)

## Configuration for additional metrics

**⚠️ Important:** some of the built‑in Grafana dashboards filter by
`site_name`/`ap_name` using the `${DS_PROMETHEUS}` variable.  UniFi site
or AP names containing parentheses, spaces or other regex metacharacters
(e.g. "Default (default)") would previously result in queries that
matched *nothing*, causing every panel to render "No data".  The
provisioned dashboards have now been updated to use the `:regex`
formatter so values are escaped automatically.  If you already have a
stack running you will need to restart the **grafana** container (or
`docker-compose up -d grafana`) so the new dashboards are re‑loaded.

Additionally, the data source name used by the dashboards is now
`DS_PROMETHEUS`.  The auto‑provisioner will delete the old `Prometheus`
entry on startup, so you can ignore the duplicate that appears in the
UI after upgrading.


### Internet health (Blackbox exporter)

1. **Enable service** – edit `compose.yaml` and make sure the `blackbox` service section is present.  It mounts `prometheus/config/blackbox.yml` which defines probe modules (icmp/http/dns).
2. **Customize probes** – update `prometheus/config/prometheus.yml` under the `blackbox` job.  Add or remove targets such as `8.8.8.8`, `1.1.1.1`, your ISP gateway, or a web host you want to monitor.  Choose modules (`icmp` for ping, `http_2xx` for web endpoints) accordingly.
3. **Reload Prometheus** – after editing `prometheus.yml` restart the `prometheus` container (`docker-compose restart prometheus`) or hit the `/-/reload` endpoint.
4. **View metrics** – browse to <http://localhost:9090/graph> and query `probe_duration_seconds` or `probe_success`.  Grafana dashboards can be extended with panels to visualize these metrics (see [Grafana comunitiy dashboards](https://grafana.com/grafana/dashboards?search=blackbox)).

### Speed test / throughput metrics (optional)

1. Uncomment the `speedtest` service in `compose.yaml` or add a different exporter of your choice.
2. After starting the container, configure a `speedtest` scrape job in `prometheus.yml` (example commented out in the file).
3. The exporter runs tests (default 15‑minute interval) and exposes metrics like `speedtest_download_bytes` and `speedtest_ping_seconds`.
4. Add Grafana panels or import a community dashboard for the exporter.

### ISP‑specific metrics (UniFi Site Manager)

UniFi's Site Manager API exposes detailed ISP metrics which can supplement your internet health picture.

**Environment variables used by the custom exporter**

- `UNIFI_API_KEY` – API key used to authenticate to the controller/cloud API.
- `EXTRA_ENDPOINTS` – comma-separated list of URLs to probe (latency metrics are emitted per endpoint).

Example (in `compose.yaml`):

```yaml
      UNIFI_API_KEY: " <REDACTED_UNIFI_API_KEY>"
      EXTRA_ENDPOINTS: "https://rdgateway.wvd.microsoft.com,https://rdbroker.wvd.microsoft.com,https://rdweb.wvd.microsoft.com"
```

UniFi's Site Manager API provides metrics such as:

- **Endpoints:**
  - `GET /site-manager/v1.0.0/getispmetrics`
  - `POST /site-manager/v1.0.0/queryispmetrics`

> If you configure **both** `UNIFI_CONTROLLER` and `UNIFI_API_URL` the exporter
> will now prefer the local controller path first and only fall back to the
> cloud API if the controller request fails.  This ensures the real‑time rates
> you obtain with the `/stat/device` curl are reflected in Prometheus even
> when you keep a cloud URL set for other reasons.

You will need a custom Prometheus exporter or script that authenticates to the controller with the key, polls these endpoints, and converts the JSON response into Prometheus metrics such as:

```text
unifi_isp_download_bytes   # export of current download rate (bytes/sec)
unifi_isp_upload_bytes     # export of current upload rate (bytes/sec)
unifi_isp_latency_ms
unifi_isp_packet_loss_percent
```

> **Note:** the exporter now calculates throughput by comparing the
> cumulative values returned by the API between scrapes.  this ensures the
> dashboard shows a changing rate rather than a static number.  Some
> controller versions instead return an instant rate (kilobits/sec); the
> exporter detects the presence of `download_kbps`/`upload_kbps` keys and
> converts them directly, so you should see non‑zero values even if the
> cloud API only reports the current speed.

If you are running the exporter against a **local UniFi controller** (not
the cloud API) the script can also log in and pull the additional
`/proxy/network/api/s/default/stat/health` endpoint.  This gives you a
second source of WAN statistics directly from the controller and mirrors
what you would get by running:

```sh
curl -k -b unifi_cookie "https://10.0.1.1/proxy/network/api/s/default/stat/health" \
  | jq '.data[] | select(.subsystem=="wan")'  # controller health endpoint
```

> **Important:** the controller requires a logged‑in session (cookie) for the
> `/stat/health` and `/stat/device` endpoints.  an API key alone usually gets
> you redirected to the login page.  set `UNIFI_USER`/`UNIFI_PASS` in
> `compose.yaml` with the same credentials you use for the curl command so the
> exporter can authenticate and pull live metrics.

> The exporter can also pull live rx/tx rates directly from the controller's
> `/proxy/network/api/s/default/stat/device` endpoint when `UNIFI_CONTROLLER` is
> set.  This mirrors the curl you ran earlier and is the source of the
> preferred download/upload metrics; the panel queries `unifi_isp_download_bytes`
> and `unifi_isp_upload_bytes` which will now be populated from either the
> site‑manager API or the device stats depending on what's available.

```

The exporter exposes the following extra metrics when the health call
succeeds:

```text
unifi_wan_status                  # 1 if controller reports "ok"
unifi_wan_tx_bytes_rate           # WAN transmit rate reported by controller
unifi_wan_rx_bytes_rate           # WAN receive rate reported by controller
unifi_wan_availability_percent    # overall WAN availability
unifi_wan_latency_ms              # overall WAN latency average
unifi_wan_monitor_latency_ms{target,type}
unifi_wan_monitor_availability_percent{target,type}
```

The health endpoint requires a valid session cookie, so you must supply
either `UNIFI_KEY` (the API key works on the controller too) or
`UNIFI_USER`/`UNIFI_PASS` environment variables in your `compose.yaml`.

### Probing arbitrary endpoints

If you'd like to track the latency of additional hosts or services, you
can specify them using the `EXTRA_ENDPOINTS` environment variable (comma‑
separated list of URLs).  The exporter will `GET` each target and expose a
latency gauge labelled by `endpoint`:

```text
unifi_endpoint_latency_seconds{endpoint="https://example.com"}
```

This is useful when you want to watch response times for an application,
 upstream CDN, or any IP that isn't covered by the blackbox probes.

Just add the variable alongside the others in `compose.yaml`:

```yaml
      EXTRA_ENDPOINTS: "https://example.com,https://api.example.net/health"
```

The metric can be graphed in Grafana like any other (see panel below).


An example key might look like:

```
Unifi_Key: "_oAzYFLN8NWQd3IhPSJx343gy3uhTXyz"
```

> **Auto‑pull exporter included**
>
> A simple Python-based Prometheus exporter lives in the `isp_exporter/` directory and is built automatically by Docker Compose. It polls the Site Manager API every minute and exposes the following metrics:
>
> ```text
> unifi_isp_download_bytes
> unifi_isp_upload_bytes
> unifi_isp_latency_ms
> unifi_isp_packet_loss_percent
> ```
>
> Configuration options are passed via environment variables in the compose file (see `UNIFI_API_KEY` and `UNIFI_CONTROLLER`), and you can adjust `POLL_INTERVAL` or `VERIFY_SSL` if needed.
>
> The exporter listens on port 9100.
>
> **Building the image:**
>
> Because Unraid’s compose manager uses Docker Buildx, the on-the-fly `build:` step may trigger an entitlement warning or fail to pull the locally-built image. To avoid that, build the image manually on your Unraid host before running the stack:
>
> 1. Copy the exporter files to the project directory Unraid is using (you likely need to create this folder if it doesn’t exist). For example:
>
>    ```bash
>    rsync -av isp_exporter/ root@<unraid>:/boot/config/plugins/compose.manager/projects/Unifi-Poller/isp_exporter/
>    # or manually create the folder and copy Dockerfile, app.py, requirements.txt
>    ```
>
> 2. Build the container image on Unraid:
>
>    ```bash
>    cd /boot/config/plugins/compose.manager/projects/Unifi-Poller/isp_exporter
>    docker build -t unpoller-unifi/isp-exporter:latest .
>    ```
>
>    (The path `/boot/config/plugins/...` may differ depending on your compose manager project name.)
>
>    > **Note:** if you modify `app.py` (or any exporter source), rebuild the
>    > image and restart the `isp_exporter` container so the changes take effect.
>    > On Unraid you can trigger this from the Compose UI or via
>    > `docker-compose build --no-cache isp_exporter && docker-compose up -d isp_exporter`.
>
> After the image exists locally, `docker-compose up` will use it without attempting to download from a registry.  You can also push the image to a public or private registry and change the `image:` field in `compose.yaml` if you prefer.

Once your exporter is running, add a scrape job to `prometheus.yml`, e.g.:

```yaml
- job_name: 'unifi_isp'
  static_configs:
    - targets: ['isp-exporter:9100']
```

and reuse the **Internet Health (Blackbox)** dashboard – it now contains panels to display ISP throughput and documentation on the panel itself.

## Troubleshooting

### UnPoller authentication

If the dashboards render "No data" but you believe the controller is still
being polled, the *most common* culprit is that UnPoller has lost its ability
to authenticate to the UniFi controller.  When this happens Prometheus will
still show the `up{job="unpoller"}` metric as `1` (because the exporter
itself is reachable) but all of the `unpoller_*` series will be empty.

To check:

```sh
# look at UnPoller logs on the host
ssh root@<unraid> docker logs unpoller --tail 20
# you should *not* see repeated 403 Forbidden errors here

# hit the exporter directly; real metrics start with "unpoller_"
curl -s http://localhost:9130/metrics | grep '^unpoller_' | head
```

If the logs contain lines like

```
[ERROR] Controller 1 of 1 Auth or Connection Error … 403 Forbidden: authentication failed
```

then update the credentials in your compose file or environment.  Example
configuration:

```yaml
services:
  unpoller:
    environment:
      UP_UNIFI_CONTROLLER_0_URL: "https://10.0.1.1"
      UP_UNIFI_CONTROLLER_0_USER: "unpoller"
      UP_UNIFI_CONTROLLER_0_PASS: "<your password>"
      UP_UNIFI_CONTROLLER_0_SITE: "default"
      UP_UNIFI_CONTROLLER_0_VERIFY_SSL: "false"  # if using self‑signed cert
```

After making any changes, restart just that service and watch its log until
it reports collecting metrics successfully.

```sh
ssh root@<unraid> "cd /path/to/project && docker-compose up -d unpoller"
ssh root@<unraid> docker logs -f unpoller
```

Once UnPoller can log in again, Prometheus will start populating the
`unpoller_*` series and the Grafana dashboards will immediately show data
for the selected time range.  (If you only have old samples, widen the
`time range` selector in Grafana to see them.)

### General tips

- Check logs: `docker-compose logs [service-name]` or use Dozzle at
  <http://localhost:8080>
- Verify UniFi controller accessibility from Docker network
- Ensure a local UniFi account has been created with read-only/view-only
  network controller access
- For 429 errors, increase scrape interval in `prometheus/config/prometheus.yml`

## Acknowledgments

This stack is built using these excellent open-source projects:

- **[UnPoller](https://github.com/unpoller/unpoller)** - Polls UniFi controllers for device and client metrics
- **[Prometheus](https://github.com/prometheus/prometheus)** - Systems monitoring and alerting toolkit
- **[Grafana](https://github.com/grafana/grafana)** - Open source analytics and interactive visualization web application
- **[Dozzle](https://github.com/amir20/dozzle)** - Real-time log viewer for Docker containers

Special thanks to the maintainers and contributors of these projects for making UniFi network monitoring accessible and powerful.
