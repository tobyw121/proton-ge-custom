---
name: Compatibility Report
about: Game compatibility issues.
---

BEFORE FILING AN ISSUE PLEASE CHECK IF THE ISSUE OCCURS ON UPSTREAM PROTON-EXPERIMENTAL FIRST.
IF IT HAPPENS ON PROTON-EXPERIMENTAL, YOU NEED TO FILE THE ISSUE UPSTREAM, NOT HERE.

UPSTREAM BUG TRACKER:
https://github.com/ValveSoftware/Proton/issues

THE GE-PROTON ISSUE TRACKER IS FOR ISSUES THAT ONLY OCCUR ON GE-PROTON BUT WORK PROPERLY UPSTREAM, OR FOR FEATURES THAT ARE ONLY SPECIFIC TO GE-PROTON SUCH AS WINE-WAYLAND.

# Compatibility Report
- Name of the game with compatibility issues:
- Steam AppID of the game:

## System Information
- GPU: <!-- e.g. RX 580 or GTX 970 -->
- Driver/LLVM version: <!-- e.g. Mesa 18.2/7.0.0 or nvidia 396.54 -->
- Kernel version: <!-- e.g. 4.17 -->
- Link to full system information report as [Gist](https://gist.github.com/):
- Proton version:

## I confirm:
- [ ] that I have verified my problem does NOT happen on proton-experimental and ONLY happens on GE-Proton
- [ ] that I am NOT using the GE-Proton flatpak. (I do not build or provide the GE-Proton flatpak and it is known to have broken codec support.)
- [ ] that I haven't found an existing compatibility report for this game.
- [ ] that I have checked whether there are updates for my system available.

For issues with the GE-Proton flatpak, report here:
https://github.com/flathub/com.valvesoftware.Steam.CompatibilityTool.Proton-GE

<!-- Please add `PROTON_LOG=1 %command%` to the game's launch options and drag
and drop the generated `$HOME/steam-$APPID.log` into this issue report -->

## Symptoms <!-- What's the problem? -->


## Reproduction


<!--
1. You can find the Steam AppID in the URL of the shop page of the game.
   e.g. for `The Witcher 3: Wild Hunt` the AppID is `292030`.
2. You can find your driver and Linux version, as well as your graphics
   processor's name in the system information report of Steam.
3. You can retrieve a full system information report by clicking
   `Help` > `System Information` in the Steam client on your machine.
4. Please copy it to your clipboard by pressing `Ctrl+A` and then `Ctrl+C`.
   Then paste it in a [Gist](https://gist.github.com/) and post the link in
   this issue.
5. Please search for open issues and pull requests by the name of the game and
   find out whether they are relevant and should be referenced above.
-->
