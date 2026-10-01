# Third-party attribution

The protocol constants, login flow and inventory request conventions in
`vendor_constants.py` and `acfreedom_energy_probe.py` are adapted from:

- Project: https://github.com/maeek/ha-aux-cloud
- Author: maeek
- Commit: 85ae111e77e92edd1b104b55a57f81ebe4c75cab
- Source: custom_components/aux_cloud/dna/http.py and dna/inventory.py
- License: MIT; full notice reproduced in LICENSE-ha-aux-cloud.txt.

Energy request field conventions are independently implemented from published
HTTP examples, not copied implementation code:
https://github.com/ZuinigeRijder/python-broadlink-smart-plug-mini

Report names and SDK limitations are described at:
https://tx.ibroadlink.com/public/appsdk_en/appsdk_05/

The v1 energy routes were experimental and are retained only under --legacy-probe.
The v2 AUX route, report names, fields and display semantics are independently
implemented from static analysis of the AC Freedom application's bundled
c0620000 product UI. See research/ENERGY_PROTOCOL.md for provenance and findings.
The application APK and its Javascript are not distributed in the Windows bundle.
