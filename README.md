# ScottyWins

Kodi 21 repository for Prime Scene (`plugin.video.primeinterface`, `script.primescene.rewrite`) and SmoothFlix (`plugin.video.smoothflix`).

Add `https://agreewithscotty.github.io/ScottyWins/` in Kodi's File manager. Install from zip file and choose `repository.scottywins-1.0.1.zip`. Then install Prime Scene and SmoothFlix from ScottyWins Repository.

An existing Prime Scene Repository or SmoothFlix Repository updates in place and is shown as ScottyWins Repository. No reinstall. `repository.scottywins-1.0.0.zip` remains at the root for the original install link.

## Release flow

On `main`, bump Prime Scene by publishing the new zip and `addons.xml`, or bump SmoothFlix in `plugin.video.smoothflix/addon.xml`. The Update repository action runs daily at 06:17 UTC and from Actions, Run workflow. It pulls both `main` branches, keeps the latest zip of each add-on, and commits `addons.xml` and `addons.xml.md5`. Kodi installs that update.

Rebuild locally with:

```bash
python3 tools/build_repo.py --primescene /path/to/PrimeScene --smoothflix /path/to/SmoothFlix
```

## Testing channel

Smooth Prime builds from the PrimeScene `testing` branch are a separate repository, `repository.scottywins.testing`, under `/testing`. The public files above are not part of that channel. Install zip: `https://agreewithscotty.github.io/ScottyWins/testing/repository.scottywins.testing-1.0.0.zip`. See [TESTING.md](TESTING.md).
