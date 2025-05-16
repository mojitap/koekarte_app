from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import StandardScaler
import numpy as np
import joblib

# 特徴量を増やした新しいデータ（例: 20次元）
X_train = [
    [0.05, 0.01, 0.7, 300, 50, 0.1, 0.2] + [0.5]*13,
    [0.08, 0.015, 0.6, 250, 40, 0.2, 0.3] + [0.3]*13,
    ...
]
y_train = [40, 35, 70, 60, 25]

# 標準化
scaler = StandardScaler()
X_scaled = scaler.fit_transform(X_train)

model = LinearRegression()
model.fit(X_scaled, y_train)

# 保存
joblib.dump((scaler, model), 'light_model.pkl')