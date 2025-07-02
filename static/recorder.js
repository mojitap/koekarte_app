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
  console.log('▶️ uploadButton clicked');
  const blob = uploadButton.blob;
  if (!blob) {
    alert("録音データがありません");
    return;
  }

  // 1) UIフィードバック
  uploadButton.disabled = true;
  statusP.textContent   = 'ただいま解析してアップロード中…';

  // 2) サーバーへ送信 (overwrite フラグなし)
  const formData = new FormData();
  formData.append('audio_data', blob, 'recording.webm');

  let res, json;
  try {
    res = await fetch('/api/upload', { method: 'POST', body: formData });
  } catch (err) {
    console.error("❌ ネットワークエラー", err);
    statusP.textContent = '';
    alert("ネットワークエラーが発生しました。");
    uploadButton.disabled = false;
    return;
  }

  if (!res.ok) {
    const text = await res.text();
    console.warn("❌ アップロード失敗", text);
    statusP.textContent = '';
    alert("アップロードに失敗しました: " + text);
    uploadButton.disabled = false;
    return;
  }

  json = await res.json();
  console.log('📤 /api/upload response:', json);

  // 3) 既存レコードあり → 上書き確認ダイアログ
  if (json.already) {
    const proceed = confirm(json.message);
    if (!proceed) {
      statusP.textContent = '';
      uploadButton.disabled = false;
      return;
    }
    // 上書きリクエスト
    try {
      res = await fetch('/api/upload?overwrite=true', { method: 'POST', body: formData });
    } catch (err) {
      console.error("❌ ネットワークエラー", err);
      statusP.textContent = '';
      alert("ネットワークエラーが発生しました。");
      uploadButton.disabled = false;
      return;
    }
    if (!res.ok) {
      const text = await res.text();
      console.warn("❌ アップロード失敗", text);
      statusP.textContent = '';
      alert("アップロードに失敗しました: " + text);
      uploadButton.disabled = false;
      return;
    }
    json = await res.json();
    console.log('📤 /api/upload overwrite response:', json);
  }

  // 4) success / job_id チェック
  if (json.success === false) {
    statusP.textContent = '';
    alert(json.message);
    uploadButton.disabled = false;
    return;
  }
  const jobId = json.job_id;
  if (!jobId) {
    statusP.textContent = '';
    alert('ジョブIDの取得に失敗しました。ページを再読み込みして再度お試しください。');
    uploadButton.disabled = false;
    return;
  }

  // 5) ポーリング開始
  statusP.textContent = 'アップロード完了。詳細解析中…';
  let tries = 0;
  const MAX_TRIES = 20;
  const poll = setInterval(async () => {
    tries++;
    let statusJ;
    try {
      const statusRes = await fetch(`/api/job_status/${jobId}`);
      if (!statusRes.ok) throw new Error('ステータス取得エラー');
      statusJ = await statusRes.json();
    } catch (err) {
      console.warn('ポーリング中のエラー（無視）', err);
      return;
    }

    if (statusJ.status === 'finished') {
      clearInterval(poll);
      statusP.textContent = `✅ 詳細解析完了！スコア：${statusJ.score} 点`;
      setTimeout(() => window.location.href = '/dashboard', 800);

    } else if (statusJ.status === 'failed') {
      clearInterval(poll);
      alert('❌ 詳細解析に失敗しました。再度お試しください。');
      window.location.href = '/dashboard';

    } else if (tries >= MAX_TRIES) {
      clearInterval(poll);
      statusP.textContent =
        '解析が長引いています…数分後にマイページで結果をご確認ください。';
    }
    // running のあいだは何もしない
  }, 1500);
});
