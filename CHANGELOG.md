# Changelog

## [0.9.0](https://github.com/OpenPowerShift/RaptorStudio/compare/v0.8.0...v0.9.0) (2026-10-31)


### Features

* check .rbds files against a JSON Schema, and write only what differs from the defaults ([296afb7](https://github.com/OpenPowerShift/RaptorStudio/commit/296afb729cd70cd2b3607680a4c11642825d81b1))
* check .rbds files against a JSON Schema, and write only what differs from the defaults ([838cf76](https://github.com/OpenPowerShift/RaptorStudio/commit/838cf76d572be3f4cad045c169758e58750b5adc)), closes [#4](https://github.com/OpenPowerShift/RaptorStudio/issues/4)
* draw the diagram as SVG without Qt ([f22c13e](https://github.com/OpenPowerShift/RaptorStudio/commit/f22c13e9bbdc126b0a06a83f419da3356f681e30))
* draw the diagram as SVG without Qt ([6460b1b](https://github.com/OpenPowerShift/RaptorStudio/commit/6460b1b9e379fbea013a97ca002b7f77b10dc976)), closes [#4](https://github.com/OpenPowerShift/RaptorStudio/issues/4)
* let cores be given back while a multi-core run is going ([55d40b7](https://github.com/OpenPowerShift/RaptorStudio/commit/55d40b7866cdd01d7dfa53ce2ad310f5df96c3ef))
* let cores be given back while a multi-core run is going ([0eebfd2](https://github.com/OpenPowerShift/RaptorStudio/commit/0eebfd231c42485ae3af401d168257a955af3a04)), closes [#11](https://github.com/OpenPowerShift/RaptorStudio/issues/11)
* say which time unit brings a too-large mean in range ([8731318](https://github.com/OpenPowerShift/RaptorStudio/commit/87313186e8029f38028dc58fef510dfa54c90412)), closes [#19](https://github.com/OpenPowerShift/RaptorStudio/issues/19)
* set-up progress, and MCP support for large models ([5d98d1d](https://github.com/OpenPowerShift/RaptorStudio/commit/5d98d1daf721fa6abaae47e8c3b6aef2466a7fb0))
* set-up progress, MCP support for large models, cheaper per-event work ([3b8c60f](https://github.com/OpenPowerShift/RaptorStudio/commit/3b8c60f2bf1ce429b92f4f1af8ead6abd9a8d5fe))
* show and edit annotation anchors in the GUI ([4abc50e](https://github.com/OpenPowerShift/RaptorStudio/commit/4abc50e505d6327635eb02a321d4470145934e5e)), closes [#4](https://github.com/OpenPowerShift/RaptorStudio/issues/4)
* show the flow a Failure Effects state delivers, in the GUI, CLI and MCP ([1b7a40d](https://github.com/OpenPowerShift/RaptorStudio/commit/1b7a40d741e5a3211ed06752491d75ea018b2edc))
* show the flow a Failure Effects state delivers, in the GUI, CLI and MCP ([dd1cff5](https://github.com/OpenPowerShift/RaptorStudio/commit/dd1cff5dff960b2cc6348518a865b20e6bb0eb76)), closes [#4](https://github.com/OpenPowerShift/RaptorStudio/issues/4)
* warn about a hierarchy marker set as a capacity boundary ([1c61570](https://github.com/OpenPowerShift/RaptorStudio/commit/1c61570df40c16c6cd0bd294376896f6b5c8e8f5))
* warn about a hierarchy marker set as a capacity boundary ([4a1bd8d](https://github.com/OpenPowerShift/RaptorStudio/commit/4a1bd8d11833901312a2f56718fe9904c6c1b98c)), closes [#4](https://github.com/OpenPowerShift/RaptorStudio/issues/4)


### Bug Fixes

* a pool's ESO/RSO/SLO flags loaded from .rbds read as set ([1876cfd](https://github.com/OpenPowerShift/RaptorStudio/commit/1876cfdae696ad59ab80e4c897c4bceb0baeb5f8))
* a pool's ESO/RSO/SLO flags loaded from .rbds read as set ([2faa1cf](https://github.com/OpenPowerShift/RaptorStudio/commit/2faa1cfefe633f10e2a9b8f7d6e18430352a79fd)), closes [#16](https://github.com/OpenPowerShift/RaptorStudio/issues/16)
* applying run options no longer re-lays the model out ([cb792c6](https://github.com/OpenPowerShift/RaptorStudio/commit/cb792c6ef1397545fddb7bb07a27479e733df671))
* applying run options no longer re-lays the model out ([5e1ee0f](https://github.com/OpenPowerShift/RaptorStudio/commit/5e1ee0fcd1d5db1485be2d6a18ceaa256db85ba3)), closes [#4](https://github.com/OpenPowerShift/RaptorStudio/issues/4)
* doctor display on Windows/macOS, a larger-unit hint for huge means, dependency docs ([fdec940](https://github.com/OpenPowerShift/RaptorStudio/commit/fdec9405e53ba4c22595d3b615f58ee5ae2f14c9))
* give the layout refine search a time budget ([16e279e](https://github.com/OpenPowerShift/RaptorStudio/commit/16e279e49998e513496a2ae726cf0b3b272b5266))
* give the layout refine search a time budget ([f894f16](https://github.com/OpenPowerShift/RaptorStudio/commit/f894f16102ce1a2a42b568f0791a1c2f0df854a3)), closes [#4](https://github.com/OpenPowerShift/RaptorStudio/issues/4)
* keep a time estimate through the importance measures ([de84118](https://github.com/OpenPowerShift/RaptorStudio/commit/de841189dae58742a1588f4a00f44a371e0f978d))
* keep a time estimate through the importance measures ([3c88f22](https://github.com/OpenPowerShift/RaptorStudio/commit/3c88f221c619765db4488135a004dcaf038101e3)), closes [#3](https://github.com/OpenPowerShift/RaptorStudio/issues/3)
* keep auto layout inside Raptor 7's canvas ([924fefb](https://github.com/OpenPowerShift/RaptorStudio/commit/924fefb733a65dac0acab7d6654412926604a075))
* keep auto layout inside Raptor 7's canvas ([a2f70de](https://github.com/OpenPowerShift/RaptorStudio/commit/a2f70de2cd936a51d08d321a8075deccb6d731ea))
* keep build_model under the complexity limit ([815eda8](https://github.com/OpenPowerShift/RaptorStudio/commit/815eda877b9656ef9e338299935e96a403fe5392))
* let a run be cancelled, or time out, while the model is being set up ([eca4a5e](https://github.com/OpenPowerShift/RaptorStudio/commit/eca4a5e2f54fa08c913b42e4188341f453a474cf))
* let a run be cancelled, or time out, while the model is being set up ([2f00f15](https://github.com/OpenPowerShift/RaptorStudio/commit/2f00f15ca408f143b6cc46cf846fa6648054259e)), closes [#15](https://github.com/OpenPowerShift/RaptorStudio/issues/15)
* lint and type errors CI found ([eb2ada9](https://github.com/OpenPowerShift/RaptorStudio/commit/eb2ada99c33d19f763e854d878eb2c46f7cb2caf))
* MCP feedback from [#4](https://github.com/OpenPowerShift/RaptorStudio/issues/4) (capacity metrics, runtime estimate, run -o, build_model hint) ([4a9846d](https://github.com/OpenPowerShift/RaptorStudio/commit/4a9846d67ad6659601c46ff5124b56fceb5cee5c))
* never re-lay out a model on save or export; anchored annotations follow their element ([3ca8cba](https://github.com/OpenPowerShift/RaptorStudio/commit/3ca8cba3fa7b8976c426332c2131950742ba40fb))
* never re-lay out a model on save or export; let annotations follow their element ([3dbf159](https://github.com/OpenPowerShift/RaptorStudio/commit/3dbf159acd370268f8b4468750f219f33740c71f)), closes [#4](https://github.com/OpenPowerShift/RaptorStudio/issues/4)
* no crash refreshing the z-order buttons while the canvas is rebuilt ([24fa0e3](https://github.com/OpenPowerShift/RaptorStudio/commit/24fa0e38d5076f737e550d5c27944c345c7a85a4))
* no crash refreshing the z-order buttons while the canvas is rebuilt ([870b8d3](https://github.com/OpenPowerShift/RaptorStudio/commit/870b8d3ae44e4cbf8f086f4bb9b375b35e94a728)), closes [#2](https://github.com/OpenPowerShift/RaptorStudio/issues/2)
* point a whole .rbds file passed to build_model at rbds_text ([708e000](https://github.com/OpenPowerShift/RaptorStudio/commit/708e0006f4db5321c8cfa465312299d8fbf0ad4c)), closes [#4](https://github.com/OpenPowerShift/RaptorStudio/issues/4)
* raise the layout refine budget to 30 s, keep 10 s on the UI thread ([b93d2e1](https://github.com/OpenPowerShift/RaptorStudio/commit/b93d2e16a0d74635f9ddc25d63d522363484b73a))
* raptor doctor reports a native display on Windows and macOS ([5e7f2e9](https://github.com/OpenPowerShift/RaptorStudio/commit/5e7f2e9edabc6d3b7cd46eb4d2befa9065314710)), closes [#8](https://github.com/OpenPowerShift/RaptorStudio/issues/8)
* raptor run -o writes the summary to the output folder ([b6272f3](https://github.com/OpenPowerShift/RaptorStudio/commit/b6272f3ebcc5a699bbaaa92d172145f7b6009b95)), closes [#4](https://github.com/OpenPowerShift/RaptorStudio/issues/4)
* read and write maintenance, sparing and the other API-named columns as .rbds fields ([f98b0ee](https://github.com/OpenPowerShift/RaptorStudio/commit/f98b0eec5879e95a7b1b56769fa5fb2fa94edbcc))
* read and write maintenance, sparing and the other API-named columns as .rbds fields ([b7ef984](https://github.com/OpenPowerShift/RaptorStudio/commit/b7ef984d1d2c5f9b06f22929b901ed5693c25fad)), closes [#4](https://github.com/OpenPowerShift/RaptorStudio/issues/4)
* report an mcp SDK that is out of range instead of crashing on it ([4ea0381](https://github.com/OpenPowerShift/RaptorStudio/commit/4ea03811e9387d5e034b527eb7bc14ec5f14f15d))
* report an mcp SDK that is out of range instead of crashing on it ([3fe3c13](https://github.com/OpenPowerShift/RaptorStudio/commit/3fe3c138580fa301da1e75cefa215c998900be10)), closes [#4](https://github.com/OpenPowerShift/RaptorStudio/issues/4)
* report capacity-weighted availability and lost flow in the run summary ([81e2af1](https://github.com/OpenPowerShift/RaptorStudio/commit/81e2af1090aad6ffebef5f4f7a0be57679613ed5)), closes [#4](https://github.com/OpenPowerShift/RaptorStudio/issues/4)
* show the capacity-weighted availability on the GUI's Capacity tab ([196bd6e](https://github.com/OpenPowerShift/RaptorStudio/commit/196bd6e856f3fd811f424c85f946e84b46133ac5)), closes [#4](https://github.com/OpenPowerShift/RaptorStudio/issues/4)
* stopping a multi-core run keeps the runs of chunks still in flight ([ee5d936](https://github.com/OpenPowerShift/RaptorStudio/commit/ee5d93637cc55b6f9d6f01757bb88b9b47a50064))
* stopping a multi-core run keeps the runs of chunks still in flight ([e2daca2](https://github.com/OpenPowerShift/RaptorStudio/commit/e2daca2ae2526f64e2c2ce0689840a1b42737ac6)), closes [#10](https://github.com/OpenPowerShift/RaptorStudio/issues/10)
* time the runtime estimate's pilot with the outputs a real batch writes ([3d91ea6](https://github.com/OpenPowerShift/RaptorStudio/commit/3d91ea697e44c846bb5293e494b533af59776b74)), closes [#4](https://github.com/OpenPowerShift/RaptorStudio/issues/4)


### Performance Improvements

* build the State_change and Red_change comments from a registry of flagged blocks ([d95a380](https://github.com/OpenPowerShift/RaptorStudio/commit/d95a3805d163a9f09d8c5a61768dd78dfe15037d))
* build the State_change and Red_change comments from a registry of flagged blocks ([18b0831](https://github.com/OpenPowerShift/RaptorStudio/commit/18b0831007b77f1dd706bc6aced52f3210ee8dd3)), closes [#17](https://github.com/OpenPowerShift/RaptorStudio/issues/17)
* cut the per-event cost that grew with the model size ([9396873](https://github.com/OpenPowerShift/RaptorStudio/commit/9396873830602b95d246279e913541b9a8949d97)), closes [#17](https://github.com/OpenPowerShift/RaptorStudio/issues/17)
* keep an index of the current instant instead of re-sorting it every step ([df86b4e](https://github.com/OpenPowerShift/RaptorStudio/commit/df86b4e824675db9bd0c006aa588c5904c001c0a))
* keep an index of the current instant instead of re-sorting it every step ([589f849](https://github.com/OpenPowerShift/RaptorStudio/commit/589f849c708c926722cc9d7f9ddd8699c1e306df)), closes [#17](https://github.com/OpenPowerShift/RaptorStudio/issues/17)
* keep set-up from being quadratic in the node count ([0533cfa](https://github.com/OpenPowerShift/RaptorStudio/commit/0533cfa68547a9658b19422eb8e22affb904b51b))
* keep set-up from being quadratic in the node count ([067b853](https://github.com/OpenPowerShift/RaptorStudio/commit/067b853e2cec8fb55d2e567a779aa21dfe6da02f)), closes [#15](https://github.com/OpenPowerShift/RaptorStudio/issues/15)
* load the model once per pool worker, not once per chunk ([a8dfc26](https://github.com/OpenPowerShift/RaptorStudio/commit/a8dfc268702126910faed75c4939bc7078347e57))
* load the model once per pool worker, not once per chunk ([09013ae](https://github.com/OpenPowerShift/RaptorStudio/commit/09013ae7651beb8f9440e46eb849b206efea52a8)), closes [#28](https://github.com/OpenPowerShift/RaptorStudio/issues/28)
* touch only the capacity group's monitors after the first pass ([3af8656](https://github.com/OpenPowerShift/RaptorStudio/commit/3af8656027b3b4e0568a4f8c573a14f4438761a9)), closes [#17](https://github.com/OpenPowerShift/RaptorStudio/issues/17)
* update capacity monitors in one pass, and only the capacity group's after the first ([88a5b2b](https://github.com/OpenPowerShift/RaptorStudio/commit/88a5b2bbe4653b56731a92c8339ed422f4ba2461))
* update the capacity monitors in one pass ([8222ad2](https://github.com/OpenPowerShift/RaptorStudio/commit/8222ad2a8f87a1a30eca48e2fc123e41d2096d2a)), closes [#17](https://github.com/OpenPowerShift/RaptorStudio/issues/17)


### Documentation

* an idle dependent breaks its path, and very large means ([2188f3e](https://github.com/OpenPowerShift/RaptorStudio/commit/2188f3ee46d23fdc2217f28f2398a3448158a87f)), closes [#20](https://github.com/OpenPowerShift/RaptorStudio/issues/20)
* canvas note -- the layout's 118.75 limit and why the bounds fix stacks on [#33](https://github.com/OpenPowerShift/RaptorStudio/issues/33) ([e12647f](https://github.com/OpenPowerShift/RaptorStudio/commit/e12647f927b9ce1f8d6493fdabae705acd6147f0))
* design note on the canvas scale, grid and annotations ([18dbe7d](https://github.com/OpenPowerShift/RaptorStudio/commit/18dbe7d6536ad48d13a4d52a13bb7779f64a9758))
* design note on the canvas scale, grid and annotations ([d338421](https://github.com/OpenPowerShift/RaptorStudio/commit/d33842113ec4dbf038452532a5a38c813bb1e663)), closes [#4](https://github.com/OpenPowerShift/RaptorStudio/issues/4)
* explain the pip commands in a virtual environment and Windows long paths ([44ec6cb](https://github.com/OpenPowerShift/RaptorStudio/commit/44ec6cb3d1ae13331035a2ccb4229bfc2a9d34f5))
* explain the pip commands in a virtual environment and Windows long paths ([8107ea5](https://github.com/OpenPowerShift/RaptorStudio/commit/8107ea53a8f81ff1a123f50f870fc1f7fc219ae4))
* how to model a block that never fails ([ebe2997](https://github.com/OpenPowerShift/RaptorStudio/commit/ebe2997187ebc6b66532de023841171d6c391ede)), closes [#4](https://github.com/OpenPowerShift/RaptorStudio/issues/4)
* rebuild manual.html for the Importance tooltip ([3f1a24c](https://github.com/OpenPowerShift/RaptorStudio/commit/3f1a24c04b67241d42944260e66de94c31267451))
* regenerate manual-reference.adoc for the Importance tooltip ([8e579b6](https://github.com/OpenPowerShift/RaptorStudio/commit/8e579b65ef75a05965dd7fb3037fdfcc70877ba2))
* revise the canvas scale note against the Raptor 7 source ([b900b65](https://github.com/OpenPowerShift/RaptorStudio/commit/b900b654c0875379d0141202efce76d92801a5e6))
* set the 0.9 codename to "Nine Nines and a Dream" ([5763204](https://github.com/OpenPowerShift/RaptorStudio/commit/5763204a1f3dcc6c68a6c1541b1dbd997a944aab))
* set the 0.9 codename to "Nine Nines and a Dream" ([c64a1c4](https://github.com/OpenPowerShift/RaptorStudio/commit/c64a1c4f811b0e3ec94d6ca48df5550f830345e4))
