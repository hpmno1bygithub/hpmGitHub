# PytoRPG Godot Phase 6

Windows Godot 4.5 / 4.5.1 專案，可用 GitHub Actions 打包 iPhone 與 iPad。

- 開啟 `project.godot` 繼續開發。
- [本版修正與 iPhone 安裝流程](docs/Phase6修正與iPhone打包.md)
- [完整開發與操作說明](README_繁體中文.md)
- [Build iOS unsigned IPA](https://github.com/hpmno1bygithub/hpmGitHub/actions/workflows/build-ios.yml)：Run workflow，成功後下載 artifact 內的 IPA，再以自己的 Apple ID 在 Sideloadly / AltStore 簽章安裝。

本版處理分層冰與融化、上升熱特效、1/3 自動踏階、原版觸控布局與 macOS 中文字型。完整原作仍有尚待移植項目，詳見驗收文件。
