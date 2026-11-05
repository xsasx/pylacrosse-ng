# pylacrosse-ng

**Modern Python library for LaCrosse / Technoline wireless sensors via JeeLink.**

> 🚧 **Status: Early Development**
>
> pylacrosse-ng is a modern successor project to the original pylacrosse library, focusing on reliable serial communication, automatic recovery and improved sensor compatibility.
>
> **The features listed below are planned and not yet implemented.**

## ✨ Project Goals

- 🔌 Reliable USB/serial communication
- 🔄 Automatic reconnection after connection loss
- 🛡️ Thread-safe receiver management and clean shutdown
- 📡 Receiver health monitoring and diagnostics
- 🌡️ LaCrosse / Technoline sensor compatibility
- 🌡️ Dual-temperature support for TX25TP-IT sensors
- 🧪 Automated parser and recovery tests
- 🐍 Modern Python packaging and CI

## 🗺️ Development Roadmap

1. Audit the original pylacrosse implementation and existing forks.
2. Establish the project structure, licensing and baseline tests.
3. Implement reliable connection handling and automatic recovery.
4. Add receiver health monitoring and diagnostics.
5. Implement and validate multi-channel sensor support.
6. Integrate with Home Assistant and prepare the first release.

## 🏠 Home Assistant Integration

This project is intended to become the foundation for:

**[LaCrosse JeeLink for Home Assistant](https://github.com/xsasx/home-assistant-lacrosse-jeelink)**

The Home Assistant integration currently uses the original pylacrosse dependency. Migration will take place after pylacrosse-ng has been implemented and successfully tested.

## 🙏 Credits & Licensing

This project builds on the concepts and behavior of the original [python-lacrosse / pylacrosse](https://github.com/hthiery/python-lacrosse) project by Heiko Thiery.

The original project is licensed under LGPL-2.1-or-later. Before incorporating original source code, the appropriate license terms, copyright notices and attribution will be included.

## ☕ Support the Project

If you find this project or my Home Assistant integrations useful and would like to support continued open-source development, you can buy me a coffee! ☕

[![Support me on Ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/xsas1337)

Thank you for supporting open-source development! ❤️

## 🤝 Contributing

Hardware testing, bug reports, protocol documentation and pull requests are welcome.

Please note that APIs and implementation details may change during early development.

## 📦 Releases

No public release is available yet. For production use, continue using the existing [LaCrosse JeeLink Home Assistant integration](https://github.com/xsasx/home-assistant-lacrosse-jeelink).
