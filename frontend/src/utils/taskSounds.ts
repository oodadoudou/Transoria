type AudioContextCtor = typeof AudioContext;

interface WindowWithWebkitAudio extends Window {
  webkitAudioContext?: AudioContextCtor;
}

const AudioCtor =
  typeof window === "undefined"
    ? null
    : window.AudioContext ??
      (window as WindowWithWebkitAudio).webkitAudioContext ??
      null;

function playTone(
  context: AudioContext,
  frequency: number,
  start: number,
  duration: number,
): void {
  const oscillator = context.createOscillator();
  const gain = context.createGain();
  oscillator.type = "sine";
  oscillator.frequency.setValueAtTime(frequency, start);
  gain.gain.setValueAtTime(0.0001, start);
  gain.gain.exponentialRampToValueAtTime(0.08, start + 0.015);
  gain.gain.exponentialRampToValueAtTime(0.0001, start + duration);
  oscillator.connect(gain).connect(context.destination);
  oscillator.start(start);
  oscillator.stop(start + duration + 0.02);
}

export function playTaskSound(kind: "success" | "attention"): void {
  if (!AudioCtor) return;
  try {
    const context = new AudioCtor();
    const now = context.currentTime;
    const pattern =
      kind === "success"
        ? [
            [660, 0, 0.12],
            [880, 0.14, 0.16],
          ]
        : [
            [440, 0, 0.12],
            [330, 0.14, 0.12],
            [220, 0.28, 0.18],
          ];
    for (const [frequency, offset, duration] of pattern) {
      playTone(context, frequency, now + offset, duration);
    }
    window.setTimeout(() => {
      void context.close();
    }, 700);
  } catch {
    // Browser audio can be blocked by the OS or webview; task state should
    // never depend on notification sound availability.
  }
}
