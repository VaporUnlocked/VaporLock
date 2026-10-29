var e = {
	status: "proposal only; descriptive types/matrices, not an executable validator",
	revision: "trusted-display-context-v1-r3",
	supersedesDirectionIndexSHA256: "d3f009a935088ba75230520dedab7e698aebab4ff20caa2f4c0ff9610c8688c7",
	wire: /* @__PURE__ */ JSON.parse("{\"encoding\":\"strict UTF-8 without BOM\",\"maxBytes\":32768,\"maxPrimitiveValues\":512,\"input\":\"private bounded immutable bytes; strict duplicate-key JSON decoder before own closed graph\",\"rootType\":\"Snapshot\",\"types\":{\"DisplayLabel\":{\"kind\":\"string\",\"minUnicodeScalars\":1,\"maxUnicodeScalars\":96,\"nullable\":true,\"wellFormedUnicodeRequired\":true,\"forbiddenRanges\":[[0,31],[127,159],[1564,1564],[8206,8207],[8232,8238],[8294,8297]],\"meaning\":\"Curated display name only; never private paths, keys, command lines, raw logs or URLs\",\"normalization\":\"none; count validated Unicode scalars, not UTF-16 units\"},\"OpaqueDisplayId\":{\"kind\":\"canonical-uuid-v4\",\"nullable\":true,\"authority\":\"none; opaque local display correlation\"},\"RefState\":{\"kind\":\"enum\",\"values\":[\"unknown\",\"none\",\"known\"]},\"Mode\":{\"kind\":\"enum\",\"values\":[\"live\",\"synthetic\"]},\"JobState\":{\"kind\":\"enum\",\"values\":[\"unknown\",\"none\",\"preparing\",\"running\",\"stopping\",\"completed\",\"cancelled\",\"failed\"]},\"RuntimeState\":{\"kind\":\"enum\",\"values\":[\"unknown\",\"checking\",\"missing\",\"installing\",\"ready\",\"failed\",\"stale\"]},\"Provider\":{\"kind\":\"enum\",\"values\":[\"unknown\",\"auto\",\"dml\",\"cuda\",\"tensorrt\",\"cpu\"]},\"ComparedProvider\":{\"kind\":\"enum\",\"values\":[\"dml\",\"cuda\",\"tensorrt\",\"cpu\"]},\"CaptureAvailability\":{\"kind\":\"enum\",\"values\":[\"unknown\",\"checking\",\"available\",\"unavailable\",\"stale\"]},\"ReviewDecision\":{\"kind\":\"enum\",\"values\":[\"unknown\",\"unreviewed\",\"accepted\",\"excluded\"]},\"UnsavedState\":{\"kind\":\"enum\",\"values\":[\"unknown\",\"clean\",\"changed\"]},\"PositiveSequence\":{\"kind\":\"integer\",\"minimum\":1,\"maximum\":9007199254740991,\"nullable\":false,\"booleansAccepted\":false},\"Ordinal\":{\"kind\":\"integer\",\"minimum\":0,\"maximum\":2147483647,\"nullable\":true,\"booleansAccepted\":false},\"CompletedProviderCount\":{\"kind\":\"integer\",\"minimum\":0,\"maximum\":4,\"nullable\":true,\"booleansAccepted\":false},\"ZoomPercent\":{\"kind\":\"integer\",\"minimum\":100,\"maximum\":300,\"nullable\":true,\"booleansAccepted\":false},\"ComparedProviders\":{\"kind\":\"array\",\"items\":\"ComparedProvider\",\"maxItems\":4,\"unique\":true,\"holesAccepted\":false},\"Version\":{\"kind\":\"literal-integer\",\"value\":1,\"booleansAccepted\":false},\"ModelRef\":{\"kind\":\"closed-object\",\"fields\":{\"state\":\"RefState\",\"displayId\":\"OpaqueDisplayId\"},\"allFieldsRequired\":true,\"additionalFields\":false,\"entityRole\":\"model\"},\"DatasetRef\":{\"kind\":\"closed-object\",\"fields\":{\"state\":\"RefState\",\"displayId\":\"OpaqueDisplayId\"},\"allFieldsRequired\":true,\"additionalFields\":false,\"entityRole\":\"dataset\"},\"RunRef\":{\"kind\":\"closed-object\",\"fields\":{\"state\":\"RefState\",\"displayId\":\"OpaqueDisplayId\"},\"allFieldsRequired\":true,\"additionalFields\":false,\"entityRole\":\"run\"},\"JobRef\":{\"kind\":\"closed-object\",\"fields\":{\"state\":\"RefState\",\"displayId\":\"OpaqueDisplayId\"},\"allFieldsRequired\":true,\"additionalFields\":false,\"entityRole\":\"job\"},\"ConfigurationRef\":{\"kind\":\"closed-object\",\"fields\":{\"state\":\"RefState\",\"displayId\":\"OpaqueDisplayId\"},\"allFieldsRequired\":true,\"additionalFields\":false,\"entityRole\":\"configuration\"},\"SourceRef\":{\"kind\":\"closed-object\",\"fields\":{\"state\":\"RefState\",\"displayId\":\"OpaqueDisplayId\"},\"allFieldsRequired\":true,\"additionalFields\":false,\"entityRole\":\"source\"},\"RuntimeRef\":{\"kind\":\"closed-object\",\"fields\":{\"state\":\"RefState\",\"displayId\":\"OpaqueDisplayId\"},\"allFieldsRequired\":true,\"additionalFields\":false,\"entityRole\":\"runtime\"},\"ImageRef\":{\"kind\":\"closed-object\",\"fields\":{\"state\":\"RefState\",\"displayId\":\"OpaqueDisplayId\"},\"allFieldsRequired\":true,\"additionalFields\":false,\"entityRole\":\"image\"},\"ClassRef\":{\"kind\":\"closed-object\",\"fields\":{\"state\":\"RefState\",\"displayId\":\"OpaqueDisplayId\"},\"allFieldsRequired\":true,\"additionalFields\":false,\"entityRole\":\"class\"},\"Outcome\":{\"kind\":\"enum\",\"values\":[\"unknown\",\"ok\",\"cancelled\",\"invalid-model\",\"invalid-dataset\",\"runtime-unavailable\",\"operation-failed\"]},\"ResultState\":{\"kind\":\"enum\",\"values\":[\"unknown\",\"none\",\"pending\",\"running\",\"passed\",\"failed\",\"cancelled\"]},\"Freshness\":{\"kind\":\"enum\",\"values\":[\"unknown\",\"current\",\"stale\"]},\"StaleReason\":{\"kind\":\"enum\",\"values\":[\"unknown\",\"none\",\"configuration-changed\"]},\"Attribution\":{\"kind\":\"enum\",\"values\":[\"unknown\",\"none\",\"current\",\"historical\"]},\"TrainingOperation\":{\"kind\":\"enum\",\"values\":[\"unknown\",\"none\",\"train\",\"dataset-check\",\"runtime-check\",\"runtime-install\"]},\"DatasetOperation\":{\"kind\":\"enum\",\"values\":[\"unknown\",\"none\",\"annotate\",\"archive\"]},\"ActualProvider\":{\"kind\":\"enum\",\"values\":[\"unknown\",\"dml\",\"cuda\",\"tensorrt\",\"cpu\"]},\"InspectOperation\":{\"kind\":\"literal-string\",\"value\":\"inspect\"},\"ValidateOperation\":{\"kind\":\"literal-string\",\"value\":\"validate\"},\"InspectionScope\":{\"kind\":\"literal-string\",\"value\":\"model-structure\"},\"ValidationScope\":{\"kind\":\"literal-string\",\"value\":\"model-runtime\"},\"DatasetCheckScope\":{\"kind\":\"literal-string\",\"value\":\"dataset-check\"},\"RuntimeFact\":{\"kind\":\"closed-object\",\"fields\":{\"revision\":\"RuntimeRef\",\"state\":\"RuntimeState\"},\"allFieldsRequired\":true,\"additionalFields\":false},\"ModelConfiguration\":{\"kind\":\"closed-object\",\"fields\":{\"model\":\"ModelRef\",\"configuration\":\"ConfigurationRef\",\"inputSettings\":\"InputConfigurationRef\",\"captureSettings\":\"CaptureConfigurationRef\",\"preferredProvider\":\"Provider\",\"runtime\":\"RuntimeFact\"},\"allFieldsRequired\":true,\"additionalFields\":false},\"InspectionResult\":{\"kind\":\"closed-object\",\"fields\":{\"operation\":\"InspectOperation\",\"scope\":\"InspectionScope\",\"operationId\":\"JobRef\",\"captured\":\"ModelConfiguration\",\"state\":\"ResultState\",\"outcome\":\"Outcome\",\"freshness\":\"Freshness\",\"staleReason\":\"StaleReason\",\"actualProvider\":\"ActualProvider\"},\"allFieldsRequired\":true,\"additionalFields\":false},\"ValidationResult\":{\"kind\":\"closed-object\",\"fields\":{\"operation\":\"ValidateOperation\",\"scope\":\"ValidationScope\",\"operationId\":\"JobRef\",\"captured\":\"ModelConfiguration\",\"state\":\"ResultState\",\"outcome\":\"Outcome\",\"freshness\":\"Freshness\",\"staleReason\":\"StaleReason\",\"actualProvider\":\"ActualProvider\"},\"allFieldsRequired\":true,\"additionalFields\":false},\"ModelInspection\":{\"kind\":\"closed-object\",\"fields\":{\"current\":\"ModelConfiguration\",\"inspectionResult\":\"InspectionResult\",\"validationResult\":\"ValidationResult\"},\"allFieldsRequired\":true,\"additionalFields\":false},\"Preflight\":{\"kind\":\"closed-object\",\"fields\":{\"scope\":\"DatasetCheckScope\",\"requestId\":\"JobRef\",\"configuration\":\"ConfigurationRef\",\"baseModel\":\"ModelRef\",\"dataset\":\"DatasetRef\",\"state\":\"ResultState\",\"outcome\":\"Outcome\",\"freshness\":\"Freshness\",\"staleReason\":\"StaleReason\"},\"allFieldsRequired\":true,\"additionalFields\":false},\"ProspectiveTraining\":{\"kind\":\"closed-object\",\"fields\":{\"run\":\"RunRef\",\"baseModel\":\"ModelRef\",\"dataset\":\"DatasetRef\",\"configuration\":\"ConfigurationRef\",\"requestedDevice\":\"DeviceRef\",\"runtimeState\":\"RuntimeState\",\"preflight\":\"Preflight\"},\"allFieldsRequired\":true,\"additionalFields\":false},\"TrainingJob\":{\"kind\":\"closed-object\",\"fields\":{\"job\":\"JobRef\",\"operation\":\"TrainingOperation\",\"run\":\"RunRef\",\"baseModel\":\"ModelRef\",\"dataset\":\"DatasetRef\",\"configuration\":\"ConfigurationRef\",\"requestedDevice\":\"DeviceRef\",\"state\":\"JobState\",\"outcome\":\"Outcome\"},\"allFieldsRequired\":true,\"additionalFields\":false},\"LocalTraining\":{\"kind\":\"closed-object\",\"fields\":{\"prospective\":\"ProspectiveTraining\",\"activeJob\":\"TrainingJob\"},\"allFieldsRequired\":true,\"additionalFields\":false},\"TrainingResult\":{\"kind\":\"closed-object\",\"fields\":{\"attribution\":\"Attribution\",\"job\":\"JobRef\",\"operation\":\"TrainingOperation\",\"run\":\"RunRef\",\"baseModel\":\"ModelRef\",\"dataset\":\"DatasetRef\",\"configuration\":\"ConfigurationRef\",\"requestedDevice\":\"DeviceRef\",\"state\":\"JobState\",\"outcome\":\"Outcome\"},\"allFieldsRequired\":true,\"additionalFields\":false},\"TrainingStatus\":{\"kind\":\"closed-object\",\"fields\":{\"selected\":\"TrainingResult\"},\"allFieldsRequired\":true,\"additionalFields\":false},\"ProviderBenchmark\":{\"kind\":\"closed-object\",\"fields\":{\"current\":\"ModelConfiguration\",\"captured\":\"ModelConfiguration\",\"benchmark\":\"JobRef\",\"comparedProviders\":\"ComparedProviders\",\"state\":\"JobState\",\"completedCount\":\"CompletedProviderCount\",\"successfulCount\":\"CompletedProviderCount\",\"outcome\":\"Outcome\",\"freshness\":\"Freshness\",\"staleReason\":\"StaleReason\"},\"allFieldsRequired\":true,\"additionalFields\":false},\"OfficialModels\":{\"kind\":\"closed-object\",\"fields\":{\"current\":\"ModelConfiguration\",\"selectedTrainingBaseModel\":\"ModelRef\"},\"allFieldsRequired\":true,\"additionalFields\":false},\"DatasetJob\":{\"kind\":\"closed-object\",\"fields\":{\"job\":\"JobRef\",\"operation\":\"DatasetOperation\",\"dataset\":\"DatasetRef\",\"annotationModel\":\"ModelRef\",\"configuration\":\"ConfigurationRef\",\"state\":\"JobState\",\"outcome\":\"Outcome\"},\"allFieldsRequired\":true,\"additionalFields\":false},\"DataCollection\":{\"kind\":\"closed-object\",\"fields\":{\"captureSource\":\"SourceRef\",\"captureAvailability\":\"CaptureAvailability\",\"dataset\":\"DatasetRef\",\"annotationModel\":\"ModelRef\",\"prospectiveConfiguration\":\"ConfigurationRef\",\"activeJob\":\"DatasetJob\"},\"allFieldsRequired\":true,\"additionalFields\":false},\"SelectedBox\":{\"kind\":\"closed-object\",\"fields\":{\"state\":\"RefState\",\"displayId\":\"OpaqueDisplayId\",\"ordinal\":\"Ordinal\"},\"allFieldsRequired\":true,\"additionalFields\":false,\"entityRole\":\"box\"},\"DataReview\":{\"kind\":\"closed-object\",\"fields\":{\"dataset\":\"DatasetRef\",\"image\":\"ImageRef\",\"selectionRevision\":\"PositiveSequence\",\"selectedBox\":\"SelectedBox\",\"activeClass\":\"ClassRef\",\"decision\":\"ReviewDecision\",\"unsavedState\":\"UnsavedState\",\"zoomPercent\":\"ZoomPercent\"},\"allFieldsRequired\":true,\"additionalFields\":false},\"Groups\":{\"kind\":\"closed-object\",\"fields\":{\"local-training\":\"LocalTraining\",\"training-status\":\"TrainingStatus\",\"model-inspection\":\"ModelInspection\",\"provider-benchmark\":\"ProviderBenchmark\",\"official-models\":\"OfficialModels\",\"data-collection\":\"DataCollection\",\"data-review\":\"DataReview\"},\"allFieldsRequired\":true,\"additionalFields\":false},\"Snapshot\":{\"kind\":\"closed-object\",\"fields\":{\"contextVersion\":\"Version\",\"rendererGeneration\":\"PositiveSequence\",\"sequence\":\"PositiveSequence\",\"mode\":\"Mode\",\"groups\":\"Groups\",\"displayProjection\":\"DisplayProjection\"},\"allFieldsRequired\":true,\"additionalFields\":false},\"InputConfigurationRef\":{\"kind\":\"closed-object\",\"fields\":{\"state\":\"RefState\",\"displayId\":\"OpaqueDisplayId\"},\"allFieldsRequired\":true,\"additionalFields\":false,\"entityRole\":\"configuration\"},\"CaptureConfigurationRef\":{\"kind\":\"closed-object\",\"fields\":{\"state\":\"RefState\",\"displayId\":\"OpaqueDisplayId\"},\"allFieldsRequired\":true,\"additionalFields\":false,\"entityRole\":\"configuration\"},\"DeviceRef\":{\"kind\":\"closed-object\",\"fields\":{\"state\":\"RefState\",\"displayId\":\"OpaqueDisplayId\"},\"allFieldsRequired\":true,\"additionalFields\":false,\"entityRole\":\"device\"},\"InputSettingsDisplay\":{\"kind\":\"closed-object\",\"fields\":{\"input\":\"DisplayLabel\",\"resize\":\"DisplayLabel\",\"output\":\"DisplayLabel\",\"coordinates\":\"DisplayLabel\"},\"allFieldsRequired\":true,\"additionalFields\":false},\"CaptureSettingsDisplay\":{\"kind\":\"closed-object\",\"fields\":{\"source\":\"DisplayLabel\",\"region\":\"DisplayLabel\",\"frameRate\":\"DisplayLabel\"},\"allFieldsRequired\":true,\"additionalFields\":false},\"KnownDisplayId\":{\"kind\":\"canonical-uuid-v4\",\"nullable\":false,\"authority\":\"none; opaque local display correlation\"},\"KnownDisplayLabel\":{\"kind\":\"string\",\"minUnicodeScalars\":1,\"maxUnicodeScalars\":96,\"nullable\":false,\"wellFormedUnicodeRequired\":true,\"forbiddenRanges\":[[0,31],[127,159],[1564,1564],[8206,8207],[8232,8238],[8294,8297]],\"meaning\":\"Curated display name only; never private paths, keys, command lines, raw logs or URLs\",\"normalization\":\"none; count validated Unicode scalars, not UTF-16 units\"},\"DisplayLocale\":{\"kind\":\"enum\",\"values\":[\"en\",\"zh-CN\",\"de\",\"ru\",\"fr\"]},\"EntityRole\":{\"kind\":\"enum\",\"values\":[\"model\",\"dataset\",\"run\",\"job\",\"configuration\",\"source\",\"runtime\",\"image\",\"class\",\"device\"]},\"EntityLabel\":{\"kind\":\"closed-object\",\"fields\":{\"displayId\":\"KnownDisplayId\",\"role\":\"EntityRole\",\"label\":\"KnownDisplayLabel\"},\"allFieldsRequired\":true,\"additionalFields\":false},\"EntityLabels\":{\"kind\":\"array\",\"itemType\":\"EntityLabel\",\"minimumItems\":0,\"maximumItems\":80,\"uniqueBy\":\"displayId\"},\"ConfigurationSummary\":{\"kind\":\"closed-object\",\"fields\":{\"configurationId\":\"KnownDisplayId\",\"inputSettings\":\"InputSettingsDisplay\",\"captureSettings\":\"CaptureSettingsDisplay\"},\"allFieldsRequired\":true,\"additionalFields\":false},\"ConfigurationSummaries\":{\"kind\":\"array\",\"itemType\":\"ConfigurationSummary\",\"minimumItems\":0,\"maximumItems\":12,\"uniqueBy\":\"configurationId\"},\"DisplayProjection\":{\"kind\":\"closed-object\",\"fields\":{\"locale\":\"DisplayLocale\",\"entityLabels\":\"EntityLabels\",\"configurationSummaries\":\"ConfigurationSummaries\"},\"allFieldsRequired\":true,\"additionalFields\":false}},\"semanticDisplaySeparation\":\"groups contain semantic facts; displayProjection is separate display-only atomic projection\"}"),
	relationMatrixFile: "relation-matrices.json",
	authorization: "no native authority; inaccessible live/synthetic receipts and per-operation native reauthorization/expected-target fences remain required",
	nativeBindingsResolved: 0,
	sourceProvenance: {
		nativeGui: {
			path: "ui\\vapor_lock_gui.py",
			sha256: "d0a02d063f020c5f6610f4a495f6dbbb64bf68ca01c6978ed5734de1c6523080",
			bytes: 555417
		},
		fixedMapIndex: {
			path: "ui_system\\proposals\\native-component-map-v1-r1\\index.json",
			sha256: "9adb329b4932c8f93cf7106c5e3dcaf8e2af68f84d2f3d99933b882c627c4710",
			bytes: 562
		},
		fixedPresentationIndex: {
			path: "ui_system\\handoffs\\native-components-v1-r2\\index.json",
			sha256: "3d66cdcc8cf3b8ddbc7e6d709a5a1bc5233f4926311ad55c1f6b1f1ab1c62236",
			bytes: 25940
		}
	}
}, t = {
	status: "DESCRIPTIVE direction proposal only; no executable relational validator or observed fixture verdict",
	ref: {
		"unknown/none": "displayId null; no label in semantic ref",
		known: "canonical lowercase UUIDv4; exactly one separately projected label except box",
		role: "derive from semantic ref TYPE; same ID always has the same role"
	},
	runtime: {
		unknown: "revision unknown",
		"checking/missing/installing/ready/failed/stale": "revision known; reported controller fact, no inferred readiness"
	},
	modelResult: {
		"unknown/none": "operationId corresponding unknown/none; blank unknown captured context; outcome/freshness/staleReason/actualProvider unknown",
		"pending/running": "operationId/model/config known; outcome unknown; captured current or explicitly stale; actualProvider unknown; input/capture setting refs known; actualProvider unknown",
		passed: "operationId/model/config known; outcome ok; inspect actualProvider unknown, validate concrete actualProvider; input/capture setting refs known",
		failed: "known operation/model/config; inspect outcome invalid-model/operation-failed; validate also runtime-unavailable; inspect actualProvider unknown; input/capture refs known; validate provider unknown unless reported by this invocation",
		cancelled: "known operation/model/config; outcome cancelled; inspect actualProvider unknown; input/capture refs known; validate provider unknown unless reported by this invocation",
		fieldScopes: "inspectionResult inspect/model-structure; validationResult validate/model-runtime; never copy inspection pass to validation",
		freshness: "captured semantic configuration equals current =>current/none; differs =>stale/configuration-changed; projection-only changes preserve it",
		clearPending: "new request/config epoch; old completions fenced; no model =>none, known model =>new pending record",
		providerProvenance: "private native invocation fact; never preferred/current inference; not proved by serialized concrete provider"
	},
	preflight: {
		"unknown/none": "requestId corresponding unknown/none; config/baseModel/dataset unknown; outcome/freshness/staleReason unknown",
		"pending/running": "known request/config/dataset, baseModel known or none, outcome unknown",
		passed: "same refs; outcome ok; dataset-check only, no model compatibility/readiness proof",
		failed: "same refs; outcome invalid-dataset/runtime-unavailable/operation-failed",
		cancelled: "same refs; outcome cancelled",
		freshness: "current config/dataset/baseModel exactly match prospective =>current/none; otherwise stale/configuration-changed preserving outcome"
	},
	trainingJob: {
		"unknown/none": "operation/all refs/DeviceRef corresponding unknown/none; outcome unknown",
		"preparing/running/stopping": "known job/config; operation-specific refs; outcome unknown",
		completed: "known job/config plus operation refs; outcome ok",
		cancelled: "known job/config plus operation refs; outcome cancelled",
		failed: "known job/config and operation-specific refs; fixed outcome from operationFailures",
		train: "known run/baseModel/dataset/DeviceRef; device projection curated, native choice captured privately",
		"dataset-check": "dataset known; run/baseModel/DeviceRef none",
		"runtime-check/runtime-install": "run/baseModel/dataset/DeviceRef none",
		selectedCurrent: "ALL fields equal local-training.activeJob; known nonempty operation",
		selectedHistorical: "known terminal completed/cancelled/failed record, same operation matrix; not current readiness",
		"selectedUnknown/None": "corresponding empty job envelope"
	},
	benchmark: {
		unknown: "benchmark unknown, captured blank unknown, list empty, counts null, outcome/freshness/staleReason unknown",
		none: "benchmark none, captured blank unknown, list empty, counts0, outcome/freshness/staleReason unknown",
		"preparing/running/stopping": "known benchmark/captured model/config,1..4 distinct providers;0<=successes<=completed<=list length; outcome unknown",
		completed: "same known refs; nonempty list; completed=list length; outcome ok iff successes>0, otherwise operation-failed/runtime-unavailable",
		failed: "known refs; empty list permitted; counts bounded; outcome invalid-model/runtime-unavailable/operation-failed",
		cancelled: "known refs; nonempty list; counts bounded; outcome cancelled",
		freshness: "captured semantic configuration equals current =>current/none; differs =>stale/configuration-changed; projection-only changes preserve it",
		zeroSuccessfulPresentation: "completed attempts with zero successes display failure; no successful-provider readiness claim"
	},
	datasetJob: {
		"unknown/none": "operation/all refs corresponding unknown/none; outcome unknown",
		"preparing/running/stopping": "known job/config/dataset; outcome unknown",
		completed: "known job/config/dataset; outcome ok",
		cancelled: "known job/config/dataset; outcome cancelled",
		failed: "known job/config/dataset; fixed outcome from operationFailures",
		annotate: "annotationModel known, including resolved detection fallback at launch",
		archive: "annotationModel none; no model passed to native package invocation"
	},
	collectionSource: {
		unknown: "unknown availability",
		none: "unknown/unavailable only; unavailable means no source selected, not observed probe failure",
		known: "any availability; never infer available from permissions"
	},
	review: {
		datasetUnknown: "image unknown; box not known; decision/unsaved unknown",
		datasetNone: "image none; box not known; decision/unsaved unknown",
		imageKnown: "dataset known",
		"imageUnknown/None": "box not known; decision/unsaved unknown; class/zoom may remain known without action target",
		boxKnown: "known dataset/image, known canonical box ID and integer ordinal0..2147483647",
		"boxUnknown/None": "ID/ordinal null",
		"nonunknownDecision/nonunknownUnsaved": "known dataset and image",
		targetChange: "advance selection revision on dataset/image/box/class/decision/unsaved target change; native expected target/revision checked independently"
	},
	crossCurrent: [
		"model-inspection.current ==provider-benchmark.current ==official-models.current in every field",
		"official-models.selectedTrainingBaseModel ==local-training.prospective.baseModel",
		"training-status selected attribution current ==local-training.activeJob in all job fields including state/outcome",
		"Equal IDs never imply equal entity role across unrelated typed refs; compare actual type role"
	],
	projection: {
		entityLabels: "exact known-ref ID/role set excluding boxes; each ID once; no extras; max80",
		configurationSummaries: "exact distinct known complete ModelConfiguration config-ID set; max12",
		sameCompleteConfiguration: "same model/input-subset/capture-subset/preferredProvider semantic facts; separately captured runtime may change revision",
		renameLocale: "projection and sequence only; all group facts, IDs, outcomes, freshness and private operation/selection epochs unchanged",
		freshness: "exact captured/current SEMANTIC configuration, never display labels or summaries",
		bounds: "entire byte/primitive limits include both groups and all projection records"
	},
	operationFailures: {
		inspect: ["invalid-model", "operation-failed"],
		validate: [
			"invalid-model",
			"runtime-unavailable",
			"operation-failed"
		],
		benchmark: [
			"invalid-model",
			"runtime-unavailable",
			"operation-failed"
		],
		train: [
			"invalid-model",
			"invalid-dataset",
			"runtime-unavailable",
			"operation-failed"
		],
		"dataset-check": [
			"invalid-dataset",
			"runtime-unavailable",
			"operation-failed"
		],
		"runtime-check": ["runtime-unavailable", "operation-failed"],
		"runtime-install": ["runtime-unavailable", "operation-failed"],
		annotate: [
			"invalid-model",
			"invalid-dataset",
			"runtime-unavailable",
			"operation-failed"
		],
		archive: ["invalid-dataset", "operation-failed"]
	},
	visibility: {
		hiddenWholePanel: "permitted; context travels with panel",
		visibleOrRevealedPanel: "protected context strip, reserved200% space, no strip-only hiding/cropping/opacity",
		nativeControls: "independent and reachable; Reveal/reset/rollback/focus contract preserved"
	}
}, n = Object.getPrototypeOf(Uint8Array.prototype), r = Object.getOwnPropertyDescriptor(n, "byteLength"), i = Object.getOwnPropertyDescriptor(n, "buffer"), a = Object.getOwnPropertyDescriptor(Map.prototype, "size");
function o(e, t) {
	if (!ArrayBuffer.isView(e) || Object.getPrototypeOf(e) !== Uint8Array.prototype) throw TypeError("Native fixed bytes required");
	let n = r.get.call(e), a = i.get.call(e);
	if (!(a instanceof ArrayBuffer) || Object.getPrototypeOf(a) !== ArrayBuffer.prototype || n === 0 || n > t) throw TypeError("Bounded private bytes required");
	let o = new Uint8Array(n);
	return Uint8Array.prototype.set.call(o, e), o;
}
function s(e, t) {
	if (!(e instanceof Map) || Object.getPrototypeOf(e) !== Map.prototype || a.get.call(e) !== t) throw TypeError("Fixed native resource map required");
	return (t) => Map.prototype.get.call(e, t);
}
//#endregion
//#region lib/builder/context/json.ts
var c = class extends Error {
	constructor(e) {
		super("Context update rejected"), this.category = e, this.code = "CONTEXT_REJECTED";
	}
};
function l(e, t) {
	if (!e) throw new c(t);
}
function u(e) {
	return typeof e == "object" && !!e && !Array.isArray(e);
}
function d(e) {
	if (typeof e == "object" && e) {
		for (let t of Object.values(e)) d(t);
		Object.freeze(e);
	}
	return e;
}
function f(e) {
	let t = /^(-?)([0-9]+)(?:\.([0-9]+))?(?:[eE]([+-]?[0-9]+))?$/.exec(e), n = (t[2] + (t[3] ?? "")).replace(/^0+/, "");
	if (!n) return 0;
	let r = Number(t[4] ?? "0");
	l(Number.isFinite(r) && Math.abs(r) <= 32768, "BOUNDS");
	let i = r - (t[3]?.length ?? 0);
	if (i < 0) {
		let e = -i;
		l(e < n.length && /^0*$/.test(n.slice(-e)), "SHAPE"), n = n.slice(0, -e);
	} else l(n.length + i <= 16, "BOUNDS"), n += "0".repeat(i);
	l(n.length <= 16, "BOUNDS");
	let a = BigInt((t[1] || "") + n), o = BigInt("9007199254740991");
	return l(a >= -o && a <= o, "BOUNDS"), Number(a);
}
function p(e) {
	l(e.byteLength > 0 && e.byteLength <= 32768, "BOUNDS"), l(!(e[0] === 239 && e[1] === 187 && e[2] === 191), "UTF8");
	let t;
	try {
		t = new TextDecoder("utf-8", {
			fatal: !0,
			ignoreBOM: !0
		}).decode(e);
	} catch {
		throw new c("UTF8");
	}
	let n = 0, r = 0, i = 0, a = () => {
		for (; n < t.length && " 	\r\n".includes(t[n]);) n++;
	}, o = () => {
		l(t[n] === "\"", "JSON");
		let e = n++, r = !1;
		for (; n < t.length;) {
			let i = t[n++];
			if (r) {
				r = !1;
				continue;
			}
			if (i === "\\") {
				r = !0;
				continue;
			}
			if (i === "\"") {
				let r;
				try {
					r = JSON.parse(t.slice(e, n));
				} catch {
					throw new c("JSON");
				}
				for (let e of r) {
					let t = e.codePointAt(0);
					l(t < 55296 || t > 57343, "JSON");
				}
				return r;
			}
		}
		throw new c("JSON");
	}, s = (e) => {
		l(e <= 16 && ++i <= 4096, "BOUNDS"), a();
		let c = t[n];
		if (c === "{") {
			n++, a();
			let r = Object.create(null);
			if (t[n] === "}") return n++, r;
			for (;;) {
				let i = o();
				l(!Object.hasOwn(r, i), "JSON"), a(), l(t[n++] === ":", "JSON"), r[i] = s(e + 1), a();
				let c = t[n++];
				if (c === "}") return r;
				l(c === ",", "JSON"), a();
			}
		}
		if (c === "[") {
			n++, a();
			let r = [];
			if (t[n] === "]") return n++, r;
			for (;;) {
				r.push(s(e + 1)), a();
				let i = t[n++];
				if (i === "]") return r;
				l(i === ",", "JSON"), a();
			}
		}
		if (l(++r <= 512, "BOUNDS"), c === "\"") return o();
		for (let [e, r] of [
			["true", !0],
			["false", !1],
			["null", null]
		]) if (t.startsWith(e, n)) return n += e.length, r;
		let u = /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/.exec(t.slice(n));
		return l(u, "JSON"), n += u[0].length, f(u[0]);
	}, d = s(1);
	return a(), l(n === t.length, "JSON"), l(u(d), "SHAPE"), d;
}
//#endregion
//#region lib/builder/context/validator-r3.ts
var m = d(e.wire.types), h = d(t.operationFailures), g = /* @__PURE__ */ new WeakSet(), _ = /* @__PURE__ */ new WeakMap(), v = Object.freeze({
	indexSHA256: "6f33c470c2070d936b86a1d117bf190a412e9cce8195c13c1a783a24b869b04c",
	contractSHA256: "2f140c2a3cd3bfa71154f579a767fe9fa12c37baa8a6f88c95f2fceb97666f78",
	relationsSHA256: "b2c2d95a383637d2fd52387b5efa0b5b402ea6727d73d8aa5c75ab78fc2e895e"
});
async function y(e) {
	let t = await crypto.subtle.digest("SHA-256", e);
	return Array.from(new Uint8Array(t), (e) => e.toString(16).padStart(2, "0")).join("");
}
async function b(e, t) {
	let n, r;
	try {
		n = o(e, 32768);
		let i = s(t, 2);
		r = [[
			"context-contract.json",
			21454,
			v.contractSHA256
		], [
			"relation-matrices.json",
			8648,
			v.relationsSHA256
		]].map(([e, t, n]) => {
			let r = i(e);
			l(r !== void 0, "SHAPE");
			let a = o(r, t);
			return l(a.byteLength === t, "SHAPE"), {
				bytes: a,
				hash: n
			};
		});
	} catch {
		throw new c("SHAPE");
	}
	l(await y(n) === v.indexSHA256, "SHAPE");
	for (let e of r) l(await y(e.bytes) === e.hash, "SHAPE");
	let i = Object.freeze({ stage: "context-resources-only" });
	return g.add(i), i;
}
function x(e) {
	return l(u(e), "SHAPE"), e;
}
function S(e, t) {
	return x(e[t]);
}
function C(e, t) {
	return l(typeof e[t] == "string", "SHAPE"), e[t];
}
function w(e, t) {
	return e[t];
}
function T(e, t, n) {
	l(w(e, t).state === n, "RELATION");
}
function E(e, t) {
	if (e === t) return !0;
	if (Array.isArray(e) || Array.isArray(t)) return Array.isArray(e) && Array.isArray(t) && e.length === t.length && e.every((e, n) => E(e, t[n]));
	if (u(e) && u(t)) {
		let n = Object.keys(e);
		return n.length === Object.keys(t).length && n.every((n) => Object.hasOwn(t, n) && E(e[n], t[n]));
	}
	return !1;
}
function D(e, t, n) {
	return n.every((n) => E(e[n], t[n]));
}
function O(e, t, n, r, i) {
	let a = m[t];
	if (l(a !== void 0, "SHAPE"), !(e === null && a.nullable)) switch (a.kind) {
		case "enum":
			l(typeof e == "string" && a.values.includes(e), "SHAPE");
			break;
		case "literal-string":
		case "literal-integer":
			l(e === a.value, "SHAPE");
			break;
		case "integer":
			l(typeof e == "number" && Number.isSafeInteger(e) && e >= a.minimum && e <= a.maximum, "SHAPE");
			break;
		case "canonical-uuid-v4":
			l(typeof e == "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(e), "SHAPE");
			break;
		case "string": {
			l(typeof e == "string", "SHAPE");
			let t = Array.from(e);
			l(t.length >= a.minUnicodeScalars && t.length <= a.maxUnicodeScalars, "SHAPE");
			for (let e of t) {
				let t = e.codePointAt(0);
				l(!a.forbiddenRanges.some(([e, n]) => t >= e && t <= n), "SHAPE");
			}
			break;
		}
		case "array": {
			l(Array.isArray(e), "SHAPE"), l(e.length >= (a.minimumItems ?? 0) && e.length <= (a.maximumItems ?? a.maxItems), "SHAPE");
			let t = /* @__PURE__ */ new Set();
			for (let o of e) if (O(o, a.itemType ?? a.items, n, r, i), a.unique || a.uniqueBy) {
				let e = a.uniqueBy ? x(o)[a.uniqueBy] : o;
				l(!t.has(e), "SHAPE"), t.add(e);
			}
			break;
		}
		case "closed-object": {
			let o = x(e), s = a.fields;
			l(Object.keys(o).length === Object.keys(s).length && Object.keys(o).every((e) => Object.hasOwn(s, e)), "SHAPE");
			for (let [e, t] of Object.entries(s)) O(o[e], t, n, r, i);
			if (a.entityRole) {
				let e = C(o, "state"), r = o.displayId;
				if (l(e === "known" ? typeof r == "string" : r === null, "RELATION"), e === "known") {
					let e = r;
					l(!n.has(e) || n.get(e) === a.entityRole, "RELATION"), n.set(e, a.entityRole);
				}
				t === "SelectedBox" && l(e === "known" ? typeof o.ordinal == "number" : o.ordinal === null, "RELATION");
			}
			if (t === "RuntimeFact") {
				let e = C(o, "state"), t = w(o, "revision");
				if (l(t.state === (e === "unknown" ? "unknown" : "known"), "RELATION"), t.state === "known") {
					let n = t.displayId;
					l(!i.has(n) || i.get(n) === e, "RELATION"), i.set(n, e);
				}
			}
			if (t === "ModelConfiguration" && w(o, "configuration").state === "known") {
				let e = w(o, "configuration").displayId, t = r.get(e);
				l(!t || D(t, o, [
					"model",
					"inputSettings",
					"captureSettings",
					"preferredProvider"
				]), "RELATION"), r.set(e, o);
			}
			break;
		}
		default: throw new c("SHAPE");
	}
}
function k(e) {
	for (let t of [
		"model",
		"configuration",
		"inputSettings",
		"captureSettings"
	]) T(e, t, "unknown");
	l(e.preferredProvider === "unknown" && S(e, "runtime").state === "unknown", "RELATION");
}
function A(e) {
	for (let t of [
		"model",
		"configuration",
		"inputSettings",
		"captureSettings"
	]) T(e, t, "known");
}
function j(e, t, n, r) {
	let i = r ? D(t, n, r) : E(t, n);
	l(e.freshness === (i ? "current" : "stale") && e.staleReason === (i ? "none" : "configuration-changed"), "RELATION");
}
function M(e, t) {
	let n = C(e, "state"), r = e.outcome;
	if ([
		"unknown",
		"none",
		"pending",
		"preparing",
		"running",
		"stopping"
	].includes(n)) l(r === "unknown", "RELATION");
	else if (["passed", "completed"].includes(n)) l(r === "ok", "RELATION");
	else if (n === "cancelled") l(r === "cancelled", "RELATION");
	else if (n === "failed") l(h[t]?.includes(r), "RELATION");
	else throw new c("RELATION");
}
function N(e, t) {
	let n = C(e, "state"), r = C(e, "operation"), i = S(e, "captured");
	if (M(e, r), n === "unknown" || n === "none") {
		T(e, "operationId", n), k(i), l(e.freshness === "unknown" && e.staleReason === "unknown" && e.actualProvider === "unknown", "RELATION");
		return;
	}
	T(e, "operationId", "known"), A(i), j(e, t, i), (r === "inspect" || n === "pending" || n === "running") && l(e.actualProvider === "unknown", "RELATION"), r === "validate" && n === "passed" && l(e.actualProvider !== "unknown", "RELATION");
}
function P(e, t) {
	let n = C(e, "state");
	if (M(e, "dataset-check"), n === "unknown" || n === "none") {
		T(e, "requestId", n);
		for (let t of [
			"configuration",
			"baseModel",
			"dataset"
		]) T(e, t, "unknown");
		l(e.freshness === "unknown" && e.staleReason === "unknown", "RELATION");
		return;
	}
	for (let t of [
		"requestId",
		"configuration",
		"dataset"
	]) T(e, t, "known");
	l(["known", "none"].includes(w(e, "baseModel").state), "RELATION"), j(e, t, e, [
		"configuration",
		"baseModel",
		"dataset"
	]);
}
function F(e, t) {
	let n = C(e, "state"), r = C(e, "operation"), i = t ? [
		"job",
		"run",
		"baseModel",
		"dataset",
		"configuration",
		"requestedDevice"
	] : [
		"job",
		"dataset",
		"annotationModel",
		"configuration"
	];
	if (n === "unknown" || n === "none") {
		l(r === n && e.outcome === "unknown", "RELATION");
		for (let t of i) T(e, t, n);
		return;
	}
	if (l(t ? [
		"train",
		"dataset-check",
		"runtime-check",
		"runtime-install"
	].includes(r) : ["annotate", "archive"].includes(r), "RELATION"), T(e, "job", "known"), T(e, "configuration", "known"), M(e, r), t) for (let t of [
		"run",
		"baseModel",
		"dataset",
		"requestedDevice"
	]) T(e, t, r === "train" || r === "dataset-check" && t === "dataset" ? "known" : "none");
	else T(e, "dataset", "known"), T(e, "annotationModel", r === "annotate" ? "known" : "none");
}
function I(e) {
	let t = C(e, "state"), n = S(e, "captured"), r = e.comparedProviders;
	if (t === "unknown" || t === "none") {
		T(e, "benchmark", t), k(n), l(r.length === 0 && e.completedCount === (t === "unknown" ? null : 0) && e.successfulCount === (t === "unknown" ? null : 0) && e.outcome === "unknown" && e.freshness === "unknown" && e.staleReason === "unknown", "RELATION");
		return;
	}
	T(e, "benchmark", "known"), A(n), l(typeof e.completedCount == "number" && typeof e.successfulCount == "number" && e.successfulCount <= e.completedCount && e.completedCount <= r.length, "RELATION"), l(t === "failed" || r.length > 0, "RELATION"), j(e, S(e, "current"), n), t === "completed" ? (l(e.completedCount === r.length, "RELATION"), l(e.successfulCount > 0 ? e.outcome === "ok" : ["operation-failed", "runtime-unavailable"].includes(e.outcome), "RELATION")) : M(e, "benchmark");
}
function L(e, t, n) {
	let r = S(e, "groups"), i = S(r, "local-training"), a = S(i, "prospective"), o = S(i, "activeJob");
	P(S(a, "preflight"), a), F(o, !0);
	let s = S(S(r, "training-status"), "selected"), c = C(s, "attribution");
	F(s, !0), l(c === "current" ? !["unknown", "none"].includes(s.state) && D(s, o, Object.keys(o)) : c === "historical" ? [
		"completed",
		"cancelled",
		"failed"
	].includes(s.state) : s.state === c, "RELATION");
	let u = S(r, "model-inspection"), d = S(u, "current"), f = S(r, "provider-benchmark"), p = S(r, "official-models");
	l(E(d, f.current) && E(d, p.current) && E(p.selectedTrainingBaseModel, a.baseModel), "RELATION"), N(S(u, "inspectionResult"), d), N(S(u, "validationResult"), d), I(f);
	let m = S(r, "data-collection");
	F(S(m, "activeJob"), !1);
	let h = w(m, "captureSource");
	h.state === "unknown" && l(m.captureAvailability === "unknown", "RELATION"), h.state === "none" && l(["unknown", "unavailable"].includes(m.captureAvailability), "RELATION");
	let g = S(r, "data-review"), _ = w(g, "dataset"), v = w(g, "image");
	_.state !== "known" && l(v.state === _.state, "RELATION"), v.state !== "known" && l(w(g, "selectedBox").state !== "known" && g.decision === "unknown" && g.unsavedState === "unknown", "RELATION"), (v.state === "known" || w(g, "selectedBox").state === "known" || g.decision !== "unknown" || g.unsavedState !== "unknown") && l(_.state === "known" && v.state === "known", "RELATION");
	let y = S(e, "displayProjection"), b = y.entityLabels, x = new Map(b.map((e) => [e.displayId, e.role])), T = [...t].filter(([, e]) => e !== "box");
	l(x.size === T.length && T.every(([e, t]) => x.get(e) === t), "RELATION");
	let O = y.configurationSummaries, k = new Set(O.map((e) => e.configurationId));
	l(k.size === n.size && [...n.keys()].every((e) => k.has(e)), "RELATION");
}
async function R(e, t) {
	l(g.has(e), "SHAPE");
	let n;
	try {
		n = o(t, 32768);
	} catch {
		throw new c("BOUNDS");
	}
	let r = p(n), i = /* @__PURE__ */ new Map(), a = /* @__PURE__ */ new Map();
	O(r, "Snapshot", i, a, /* @__PURE__ */ new Map()), L(r, i, a), d(r);
	let s = await y(n), u = Object.freeze({ stage: "context-display-only" });
	return _.set(u, {
		snapshot: r,
		sourceSHA256: s,
		roles: i
	}), u;
}
function z(e) {
	let t = _.get(e);
	return l(t, "SHAPE"), t.snapshot;
}
function B(e) {
	let t = _.get(e);
	return l(t, "SHAPE"), t.sourceSHA256;
}
function V(e) {
	let t = _.get(e);
	return l(t, "SHAPE"), new Map(t.roles);
}
function H(e, t) {
	let n = z(e), r = z(t);
	return E(n.groups, r.groups) && n.rendererGeneration === r.rendererGeneration && n.mode === r.mode && typeof n.sequence == "number" && typeof r.sequence == "number" && r.sequence > n.sequence;
}
//#endregion
export { c as ContextError, H as contextDisplayOnlyTransition, v as contextIdentity, V as contextRoles, z as contextSnapshot, B as contextSourceSHA256, R as inspectContextBytes, b as prepareContextResources };

//# sourceMappingURL=context.mjs.map