let mediaRecorder;
let recordedChunks = [];

// 一度だけ要素を取得
const recordButton  = document.getElementById('recordButton');
const stopButton    = document.getElementById('stopButton');
const uploadButton  = document.getElementById('uploadButton');
const audioPlayback = document.getElementById('audioPlayback');
const statusP       = document.getElementById('uploadStatus');

// デバッグ：要素がちゃんと取れているかチェック
console.log('📣 recorder.js loaded:', {
  recordButton,
  stopButton,
  uploadButton,
  audioPlayback,
  statusP
});

let timerInterval, autoStopTimer, seconds;

function startTimer() {
  seconds = 0;
  timerInterval = setInterval(() => {
    seconds++;
    audioPlayback.innerText = `録音中: ${seconds}秒`;
  }, 1000);
}

function stopTimer() {
  clearInterval(timerInterval);
  audioPlayback.innerText = "";
}

recordButton.addEventListener('click', async () => {
  console.log('▶️ recordButton clicked');
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    mediaRecorder = new MediaRecorder(stream, {
      mimeType: 'audio/webm;codecs=opus',
      audioBitsPerSecond: 128000
    });
    recordedChunks = [];

    stream.getAudioTracks()[0].onended = () => {
      alert("⚠️ マイクが途中で切断されました。");
      stopTimer();
      recordButton.disabled = false;
      stopButton.disabled = true;
    };

    mediaRecorder.addEventListener('dataavailable', e => {
      if (e.data.size > 0) recordedChunks.push(e.data);
    });

    mediaRecorder.addEventListener('stop', () => {
      const blob = new Blob(recordedChunks, { type: 'audio/webm' });
      if (!blob.size) {
        alert("⚠️ 録音できていません。");
        return;
      }
      audioPlayback.src = URL.createObjectURL(blob);
      audioPlayback.load();
      uploadButton.disabled = false;
      uploadButton.blob = blob;
    });

    mediaRecorder.start();
    startTimer();
    recordButton.disabled = true;
    stopButton.disabled = false;

    autoStopTimer = setTimeout(() => {
      if (mediaRecorder.state === "recording") {
        mediaRecorder.stop();
        stopTimer();
        recordButton.disabled = false;
        stopButton.disabled = true;
      }
    }, 60000);

  } catch (err) {
    console.error("❌ マイク取得エラー", err);
    alert("マイクの取得に失敗しました。ブラウザ設定を確認してください。");
  }
});

stopButton.addEventListener('click', () => {
  console.log('▶️ stopButton clicked');
  if (mediaRecorder && mediaRecorder.state === "recording") {
    clearTimeout(autoStopTimer);
    mediaRecorder.stop();
    stopTimer();
    recordButton.disabled = false;
    stopButton.disabled = true;
  }
});

uploadButton.addEventListener('click', async () => {
  // 録音データがなければ終了
  const blob = uploadButton.blob;
  if (!blob) { return alert("録音データがありません"); }

  uploadButton.disabled = true;
  statusP.textContent   = 'ただいま解析してアップロード中…';

  const formData = new FormData();
  formData.append('audio_data', blob, 'recording.webm');

  let res, json;

  // --- Step 1: 初回アップロード ---
  try {
    res = await fetch('/api/upload', { method: 'POST', body: formData });
  } catch (err) {
    console.error(err);
    statusP.textContent = '';
    uploadButton.disabled = false;
    return alert("ネットワークエラーが発生しました。");
  }
  if (!res.ok) {
    const text = await res.text();
    statusP.textContent = '';
    uploadButton.disabled = false;
    return alert("アップロードに失敗しました: " + text);
  }
  json = await res.json();

  // --- Step 2: 当日分既存なら「上書きしますか？」を確認 ---
  if (json.already) {
    const ok = confirm(json.message);
    if (!ok) {
      statusP.textContent = '';
      uploadButton.disabled = false;
      return;
    }
    // 上書きリクエスト
    res = await fetch('/api/upload?overwrite=true', { method: 'POST', body: formData });
    if (!res.ok) {
      const text = await res.text();
      statusP.textContent = '';
      uploadButton.disabled = false;
      return alert("上書きアップロードに失敗しました: " + text);
    }
    json = await res.json();
  }

  // --- Step 3: 成功／job_idをチェック ---
  if (json.success === false) {
    statusP.textContent = '';
    uploadButton.disabled = false;
    return alert(json.message || json.error || '上書き時にエラーが発生しました。');
  }
  const jobId = json.job_id;
  if (!jobId) {
    statusP.textContent = '';
    uploadButton.disabled = false;
    return alert('ジョブIDの取得に失敗しました。再読み込みしてください。');
  }

  // --- Step 4: ポーリング開始 ---
  statusP.textContent = 'アップロード完了。詳細解析中…';
  let tries = 0;
  const poll = setInterval(async () => {
    tries++;
    let statusJ;
    try {
      const statusRes = await fetch(`/api/job_status/${jobId}`);
      statusJ = await statusRes.json();
    } catch {
      return; // 一時的エラーは無視
    }
    if (statusJ.status === 'finished') {
      clearInterval(poll);
      statusP.textContent = `✅ 詳細解析完了！スコア：${statusJ.score} 点`;
      setTimeout(() => location.href = '/dashboard', 800);
    } else if (statusJ.status === 'failed') {
      clearInterval(poll);
      return alert('詳細解析に失敗しました。再度お試しください。');
    } else if (tries >= 20) {
      clearInterval(poll);
      statusP.textContent =
        '解析が長引いています…数分後にマイページでご確認ください。';
    }
  }, 1500);
});
