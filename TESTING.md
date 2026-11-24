# ScottyWins Testing

The public channel (`addons.xml` at the site root, `repository.scottywins`) builds Smooth Prime from the PrimeScene `release` branch and SmoothFlix from SmoothFlix `main`. It does not read PrimeScene `main`.

The test channel is a separate Kodi repository on the same GitHub Pages site. It lists Smooth Prime (`plugin.video.primeinterface`) from the PrimeScene `testing` branch, plus `repository.scottywins.testing`. It does not list SmoothFlix or the public repository add-ons.

Install in Kodi's File manager from `https://agreewithscotty.github.io/ScottyWins/testing/` and choose `repository.scottywins.testing-1.0.1.zip`. The trailing slash matters: Kodi appends the filename to the source path. Direct install zip:

https://agreewithscotty.github.io/ScottyWins/testing/repository.scottywins.testing-1.0.1.zip

`repository.scottywins.testing-1.0.0.zip` stays in that folder for the original install link. A copy already installed at 1.0.0 keeps its Pages datadir until Kodi updates the repository add-on to 1.0.1. That update is a normal add-on update. It does not need a reinstall.

GitHub Pages for this repo publishes the `main` branch only. Those URLs answer after this is on `main`.

## Version numbers

Kodi stores one copy of `plugin.video.primeinterface`. It updates that copy only when a repository offers a **greater** version than the one already installed. The public and testing channels share that add-on id, so a testing build published at the same version as the public channel (or as the build already on the box) does not replace it.

PrimeScene release versions are three dotted numbers, `X.Y.Z`. Bump that version on the testing branch before you push. Example: public is `0.2.47`, so the testing build has to be `0.2.48` or higher. A `~` suffix sorts as older in Kodi, and the PrimeScene packager accepts only `X.Y.Z`.

`tools/build_repo.py` prints a warning when the testing version is not greater than the version in the public `addons.xml`. The build still finishes, so a first publish of an equal version can seed the test channel for a box that does not have Smooth Prime yet.

## Push a test build

On PrimeScene, check out `testing` and publish Smooth Prime the same way you publish `main`: `packages/plugin.video.primeinterface/plugin.video.primeinterface-X.Y.Z.zip` and the matching entry in `addons.xml`. The zip's own `addon.xml` version has to match. Then:

```bash
git push origin testing
```

If `testing` does not exist yet, create it from current `main` with no other changes (`git push origin main:testing`) and bump the version on the next commit.

## Rebuild the test channel

The Update repository action (daily at 06:17 UTC, and Actions → Run workflow) pulls PrimeScene `release`, SmoothFlix `main`, and PrimeScene `testing`, then rebuilds both channels. When `testing` does not exist, the action says so and builds the test channel from PrimeScene `main`. When `release` does not exist, the action fails and the published public files stay as they are.

Test channel only, which does not rewrite the public index:

```bash
python3 tools/build_repo.py --testing-primescene /path/to/PrimeScene
```

The checkout has to be on branch `testing`. A checkout on another branch is used as the source and the script says which branch that was.

Public and test together:

```bash
python3 tools/build_repo.py \
  --primescene /path/to/PrimeScene-release \
  --smoothflix /path/to/SmoothFlix \
  --testing-primescene /path/to/PrimeScene-testing
```

The test output is `testing/addons.xml`, `testing/addons.xml.md5`, the zips under `testing/`, and `testing/index.html` (the File manager link). Commit those files. The public root files come from PrimeScene `release` and SmoothFlix `main` only.

## Promote to the public channel

Follow the Promote steps in the ScottyWins README: open a pull request from PrimeScene `testing` into `release`, use **Merge pull request**, then run the Update repository workflow. The public rebuild does not copy `testing/addons.xml` onto the public index.

After the public channel is serving that version, the next test build needs a higher `X.Y.Z` again.

## Download counts

Kodi builds each package URL as `datadir + addon-id + "/" + addon-id + "-" + version + ".zip"`. From repository 1.0.1 the testing datadir is `https://github.com/AgreeWithScotty/ScottyWins/releases/download/`, so Smooth Prime is:

https://github.com/AgreeWithScotty/ScottyWins/releases/download/plugin.video.primeinterface/plugin.video.primeinterface-X.Y.Z.zip

That URL is the GitHub Release tagged `plugin.video.primeinterface`, asset `plugin.video.primeinterface-X.Y.Z.zip`. The Update repository workflow uploads the asset when it rebuilds the test channel. It does not replace an asset that is already there, because replacing it resets `download_count`. The same zip remains under `testing/plugin.video.primeinterface/` for 1.0.0 clients.

Icon and fanart URLs stay under `https://agreewithscotty.github.io/ScottyWins/testing/`. The changelog Kodi shows for the test build is the `<news>` text in `testing/addons.xml`.

Read the counts from:

https://api.github.com/repos/AgreeWithScotty/ScottyWins/releases/tags/plugin.video.primeinterface

Each entry in `assets` has `name` and `download_count`. One asset is one version.
