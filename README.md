# Shelf Roulette

Shelf Roulette is a wheel of book spines that picks what you read next. It reads your Goodreads to-read shelf live, so there's no CSV to export, and it never writes anything back. It runs as two small containers on your own machine, and the browser only ever talks to your server.

![Platform](https://img.shields.io/badge/platform-linux%2Famd64%20%7C%20arm64-0078D6?style=flat-square&logo=docker&logoColor=white)
![Release](https://img.shields.io/github/v/release/nullroute727/shelf-roulette?style=flat-square&color=orange)
[![CI](https://img.shields.io/github/actions/workflow/status/nullroute727/shelf-roulette/ci.yml?branch=main&style=flat-square&label=ci)](https://github.com/nullroute727/shelf-roulette/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg?style=flat-square)](./LICENSE)

![The wheel in readable-set mode, eighteen book spines with jacket art and titles](docs/wheel.jpg)

## Run it locally

All you need is Docker with the Compose plugin. There's no Node, Python, or build step. Copy the example config and set `GOODREADS_USER_ID` to your Goodreads user id.

```bash
git clone https://github.com/nullroute727/shelf-roulette.git
cd shelf-roulette
cp .env.example .env
```

To find your id, open your Goodreads profile. In a URL like `https://www.goodreads.com/user/show/12345678-yourname`, the digits before the dash are the id. Then pull the published images and start the containers.

```bash
docker compose pull
docker compose up -d
```

Open [the wheel](http://localhost:8088) in your browser, or `http://<host>:8088` from another machine on your network. Until the id is set, the page tells you it's missing.

The shelf has to be public for the feed to return anything. If your profile is private, open the shelf on Goodreads, copy the `key=` value from the RSS link at the bottom of the page, and set it as `GOODREADS_RSS_KEY`.

## Spin the wheel

1. Tap the wheel to spin it, or flick it and let the throw carry the spin.
2. Read the result slip. It shows the cover, title, author, year, page count, average rating, and a link to the book on Goodreads.
3. Click **Reading this** to stop a book coming up again. It's hidden in your browser only, and **Clear hidden list** in the footer brings everything back.
4. Click **Reshuffle** for a new order, or **Refresh from Goodreads** if you've just added books.

![The result slip: cover, title, author, year, page count, average rating, and a link to Goodreads](docs/slip.jpg)

## Whole shelf or a readable set

- **Whole shelf** is the default. Every book gets its own spine and an equal chance. On a shelf of a few hundred books, the wedges are too thin for titles, so the wheel shows coloured spines and marks the winner with a brass tab.
- **Readable set** draws a slice that fits the screen: 18 spines on desktop and 10 on narrow phones. Titles fit on the spines, and on wider screens each wedge shows the book's cover. The whole shelf is still the pool.
- Switch between them with the lamp under the spin button. The choice is remembered. Titles, separators, and covers turn on whenever the wedges are wide enough, so a shrinking shelf starts showing titles again on its own.
- With `prefers-reduced-motion`, the wheel goes straight to the result. Every control is at least 44px tall, focus rings are visible, and the winner is announced through an `aria-live` region.
- Brave and Chrome offer **Install app**. iOS only offers **Add to Home Screen** over HTTPS, which the Tailscale setup below gives you.

## Use it from your phone over Tailscale

To spin the wheel away from your home network, publish it to your tailnet with Tailscale Serve. Check that the port is free first with `tailscale serve status` and `ss -ltn`.

```bash
sudo tailscale serve --bg --https=8443 http://127.0.0.1:8088
```

Open `https://<machine>.<tailnet>.ts.net:8443`. It's only reachable from your tailnet, not the public internet, and Serve provides a real TLS certificate. The setting survives a reboot. To remove it, run `sudo tailscale serve --https=8443 off`.

If the address doesn't load on your phone, make sure Tailscale is connected, **Use Tailscale DNS** is on, and the URL includes the port.

Phone connections over a tunnel drop requests quietly when the network changes or a tab goes to the background. The app waits up to 12 seconds for the shelf, retries three times, and reloads when the device comes back online. The proxy refreshes the shelf before its cache expires, so it usually answers from memory in a few milliseconds.

## What does it reach?

Goodreads retired its public API in 2020, but each shelf still has an RSS feed. Browsers can't fetch that feed directly, so `roulette-proxy` fetches it on the server, walks the pages, and serves the shelf as JSON. It streams cover images through the same way, so your browser never contacts Goodreads.

```
browser  ->  roulette-web (nginx :8080, published on host :8088)
                 |  static files, vendored fonts, /api/* reverse proxy
                 v
             roulette-proxy (FastAPI :8000)
                 |  paginated RSS fetch, in-memory shelf cache, on-disk cover cache
                 v
             goodreads.com, gr-assets.com
```

Only the proxy goes out to the internet, and only to those two hosts. The cover endpoint rejects any other host, so it can't be used as an open proxy. The proxy fetches one page at a time with a pause between pages and caches the shelf for an hour by default. It holds no credentials apart from the optional feed key. The fonts are served from your own server, so pages never call Google Fonts.

| Endpoint | What it returns |
| --- | --- |
| `GET /api/shelf` | The shelf as a JSON array. Add `?refresh=1` to skip the cache. |
| `GET /api/cover?url=...` | A cover image, limited to Goodreads hosts and cached on disk. |
| `GET /api/health` | `200` without touching the network. |

If Goodreads is slow or down, `/api/shelf` serves the last good copy with an `X-Shelf-Stale: 1` header. With no copy to fall back on, it returns a `502` with a JSON error that the page displays.

## Development and configuration

Run `docker compose up -d --build` to build from source instead of pulling images. `docker compose logs -f proxy` follows the proxy, and `docker compose restart proxy` clears the shelf cache. `docker compose down -v` also deletes the cover cache. Both services restart on their own after a reboot.

Settings live in `.env`. [`.env.example`](./.env.example) explains each one.

| Setting | What it controls |
| --- | --- |
| `GOODREADS_USER_ID` | Goodreads user id whose shelf you spin. Required. |
| `GOODREADS_SHELF` | Shelf to read. Defaults to `to-read`. |
| `GOODREADS_RSS_KEY` | Feed key for a private profile. Empty by default. |
| `SHELF_TTL_SECONDS` | How long the shelf is cached in memory. Defaults to `3600`. |
| `HOST_PORT` | Host port for the page. Defaults to `8088`. |
| `LOG_LEVEL` | Proxy log level. Defaults to `info`. |
| `SHELF_ROULETTE_TAG` | Image tag to run. Defaults to `latest`. Set a version such as `1.0.0` to pin it. |

Releases are published to GHCR as `ghcr.io/nullroute727/shelf-roulette-proxy` and `ghcr.io/nullroute727/shelf-roulette-web` for `linux/amd64` and `linux/arm64`, so they run on a Raspberry Pi. Pushing a `vX.Y.Z` tag builds and publishes both.

## License

Shelf Roulette is available under the [MIT License](./LICENSE). The Zilla Slab and Barlow Condensed fonts are included under the SIL Open Font License 1.1. Their notices are in [`web/site/fonts/OFL.txt`](./web/site/fonts/OFL.txt).

This is an unofficial personal project with no affiliation with Goodreads or Amazon. Book details and cover art belong to Goodreads and their rights holders.
