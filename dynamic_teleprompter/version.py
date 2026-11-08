"""Single release version; changing this on main triggers a tested release."""
APP_NAME = 'Cosmere Video Recording Suite'
VERSION = '0.5.0-alpha25'
RELEASE_NOTES = 'Retake detection now handles longer restarts, consecutive attempts, transcription errors and curly apostrophes. Uncertain cuts remain reviewable.\nSpeech and restarts are analyzed before pause trimming, then mapped onto the baseline timeline.\nEditable recording captures one lossless layered stream and prepares export tracks after Stop. Cached fade/glow rendering and transparent model-margin cropping reduce repeated work.\nDiagnostics now show fresh capture FPS, duplicate-frame percentage and average canvas stages. Originals, baseline exports, approval and explicit Discard are preserved.'
