# ScottyWins

Kodi 21 repository for Prime Scene (`plugin.video.primeinterface`, `script.primescene.rewrite`) and SmoothFlix (`plugin.video.smoothflix`).

Add `https://agreewithscotty.github.io/ScottyWins/` in Kodi's File manager. Install from zip file and choose `repository.scottywins-1.0.1.zip`. Then install Prime Scene and SmoothFlix from ScottyWins Repository.

An existing Prime Scene Repository or SmoothFlix Repository updates in place and is shown as ScottyWins Repository. No reinstall. `repository.scottywins-1.0.0.zip` remains at the root for the original install link.

## Release flow

New Smooth Prime builds go to the PrimeScene `testing` branch first. The public channel publishes Smooth Prime only from the PrimeScene `release` branch, and SmoothFlix from SmoothFlix `main`. It does not read PrimeScene `main`. The Update repository action runs daily at 06:17 UTC and from Actions, Run workflow. If `release` does not exist, that action fails and the files already on the site stay as they are.

Rebuild locally with a checkout of PrimeScene `release`:

```bash
python3 tools/build_repo.py --primescene /path/to/PrimeScene-release --smoothflix /path/to/SmoothFlix
```

## Promote

This makes PrimeScene `release` match `testing`, then publishes that commit on the public channel.

1. Open https://github.com/AgreeWithScotty/PrimeScene/compare/release...testing
2. Confirm the base branch is `release` and the compare branch is `testing`.
3. Click **Create pull request**.
4. Click **Create pull request** again.
5. Click **Merge pull request**, then **Confirm merge**.
6. Open https://github.com/AgreeWithScotty/ScottyWins/actions/workflows/update-repo.yml
7. Click **Run workflow**. Leave the branch set to `main`. Click **Run workflow**.

## Testing channel

Smooth Prime builds from the PrimeScene `testing` branch are a separate repository, `repository.scottywins.testing`, under `/testing`. The public files above are not part of that channel. Install zip: `https://agreewithscotty.github.io/ScottyWins/testing/repository.scottywins.testing-1.0.1.zip`. The 1.0.0 zip remains there for the original install link. See [TESTING.md](TESTING.md).
