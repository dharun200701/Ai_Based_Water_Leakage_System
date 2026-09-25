import pandas as pd

from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix

import matplotlib.pyplot as plt

# Load Dataset
df = pd.read_csv("location_aware_gis_leakage_dataset_with_units.csv")

print(df.head())

# Features
X = df[
    [
        "Pressure_PSI",
        "Flow_Rate_Lmin",
        "Temperature_C",
        "Vibration_mms",
        "RPM",
        "Operational_Hours_h"
    ]
]

# Target
y = df["Leakage_Flag"]

# Split
X_train, X_test, y_train, y_test = train_test_split(
    X,
    y,
    test_size=0.2,
    random_state=42
)

# Model
model = RandomForestClassifier(
    n_estimators=100,
    random_state=42
)

model.fit(X_train, y_train)

# Prediction
y_pred = model.predict(X_test)

# Accuracy
accuracy = accuracy_score(y_test, y_pred)

print("Accuracy:", accuracy)

print(classification_report(y_test, y_pred))

# Confusion Matrix
cm = confusion_matrix(y_test, y_pred)

print(cm)

plt.imshow(cm, cmap="Blues")
plt.title("Confusion Matrix")
plt.xlabel("Predicted")
plt.ylabel("Actual")
plt.colorbar()
plt.show()

# Example Prediction

sample = [[45, 120, 28, 0.45, 1600, 2500]]

prediction = model.predict(sample)

if prediction[0] == 1:
    print("\n🚨 Leak Detected")
else:
    print("\n✅ No Leak")