
document.addEventListener(
    "DOMContentLoaded",
    function () {

        loadDashboardData();

        setupPredictionForm();

        setupRefreshButton();

        setupNavigation();

    }
);


/* =========================================================
   LOAD DASHBOARD DATA
   ========================================================= */

async function loadDashboardData() {

    try {

        const response =
            await fetch(
                "/api/civil-data"
            );


        if (response.status === 401) {

            window.location.href =
                "/login";

            return;
        }


        if (!response.ok) {

            throw new Error(
                "Could not load network data."
            );
        }


        const data =
            await response.json();


        updateStatistics(data);

        updateLeakTable(
            data.records
        );


    } catch (error) {

        console.error(
            "Dashboard error:",
            error
        );

        showTableError(
            "Unable to load network data."
        );

    }

}


/* =========================================================
   UPDATE STATISTICS
   ========================================================= */

function updateStatistics(data) {

    const total =
        document.getElementById(
            "total-records"
        );

    const leaks =
        document.getElementById(
            "leak-count"
        );

    const normal =
        document.getElementById(
            "normal-count"
        );

    const rate =
        document.getElementById(
            "leak-rate"
        );


    if (total) {

        total.textContent =
            data.total_records.toLocaleString();

    }


    if (leaks) {

        leaks.textContent =
            data.leaks.toLocaleString();

    }


    if (normal) {

        normal.textContent =
            data.normal.toLocaleString();

    }


    if (rate) {

        rate.textContent =
            `${data.leak_rate}%`;

    }

}


/* =========================================================
   UPDATE LEAK TABLE
   ========================================================= */

function updateLeakTable(records) {

    const tbody =
        document.getElementById(
            "leak-table-body"
        );


    if (!tbody) {

        return;

    }


    if (
        !records ||
        records.length === 0
    ) {

        tbody.innerHTML = `

            <tr>

                <td
                    colspan="7"
                    class="table-loading"
                >
                    No leakage cases found.
                </td>

            </tr>

        `;

        return;

    }


    tbody.innerHTML = records.map(
        record => {

            return `

                <tr>

                    <td>
                        ${escapeHtml(
                            record.Location_Code ?? "-"
                        )}
                    </td>

                    <td>
                        ${escapeHtml(
                            record.Zone ?? "-"
                        )}
                    </td>

                    <td>
                        ${escapeHtml(
                            record.Block ?? "-"
                        )}
                    </td>

                    <td>
                        ${escapeHtml(
                            record.Pipe ?? "-"
                        )}
                    </td>

                    <td>
                        ${formatNumber(
                            record.Pressure_PSI
                        )} PSI
                    </td>

                    <td>
                        ${formatNumber(
                            record.Flow_Rate_Lmin
                        )} L/min
                    </td>

                    <td>

                        <span class="table-status">

                            ⚠ Leak

                        </span>

                    </td>

                </tr>

            `;

        }
    ).join("");

}


/* =========================================================
   PREDICTION FORM
   ========================================================= */

function setupPredictionForm() {

    const form =
        document.getElementById(
            "prediction-form"
        );


    if (!form) {

        return;

    }


    form.addEventListener(
        "submit",
        async function (event) {

            event.preventDefault();


            const button =
                document.getElementById(
                    "predict-button"
                );

            const text =
                document.getElementById(
                    "predict-text"
                );

            const loader =
                document.getElementById(
                    "predict-loader"
                );


            // Loading state

            button.disabled = true;

            text.textContent =
                "Analyzing sensor data...";

            loader.classList.remove(
                "hidden"
            );


            try {

                const data =
                    getFormData();


                const response =
                    await fetch(
                        "/predict",
                        {
                            method: "POST",

                            headers: {
                                "Content-Type":
                                    "application/json"
                            },

                            body:
                                JSON.stringify(data)
                        }
                    );


                if (
                    response.status === 401
                ) {

                    window.location.href =
                        "/login";

                    return;

                }


                const result =
                    await response.json();


                if (!response.ok) {

                    throw new Error(
                        result.error ||
                        "Prediction failed."
                    );

                }


                displayPrediction(
                    result
                );


            } catch (error) {

                displayPredictionError(
                    error.message
                );


            } finally {

                button.disabled = false;

                text.textContent =
                    "Run Leakage Check";

                loader.classList.add(
                    "hidden"
                );

            }

        }
    );

}


/* =========================================================
   GET FORM DATA
   ========================================================= */

function getFormData() {

    return {

        pressure:
            Number(
                document.getElementById(
                    "pressure"
                ).value
            ),

        flow:
            Number(
                document.getElementById(
                    "flow"
                ).value
            ),

        temperature:
            Number(
                document.getElementById(
                    "temperature"
                ).value
            ),

        vibration:
            Number(
                document.getElementById(
                    "vibration"
                ).value
            ),

        rpm:
            Number(
                document.getElementById(
                    "rpm"
                ).value
            ),

        hours:
            Number(
                document.getElementById(
                    "hours"
                ).value
            )

    };

}


/* =========================================================
   DISPLAY PREDICTION
   ========================================================= */

function displayPrediction(data) {

    const result =
        document.getElementById(
            "result"
        );


    const probability =
        document.getElementById(
            "probability"
        );


    const probabilityFill =
        document.getElementById(
            "probability-fill"
        );


    const notificationBox =
        document.getElementById(
            "notification-box"
        );


    const notificationMessage =
        document.getElementById(
            "notification-message"
        );


    const notificationStatus =
        document.getElementById(
            "notification-status"
        );


    const resultTime =
        document.getElementById(
            "result-time"
        );


    /* -------------------------------
       RESULT
       ------------------------------- */

    const isLeak =
        data.status === "danger";


    result.className =
        `result ${
            isLeak
                ? "danger"
                : "safe"
        }`;


    result.innerHTML = `

        <div class="result-icon">

            ${
                isLeak
                    ? "⚠"
                    : "✓"
            }

        </div>


        <div>

            <strong>
                ${escapeHtml(
                    data.prediction
                )}
            </strong>

            <small>
                Leak probability:
                ${data.probability}%
            </small>

        </div>

    `;


    /* -------------------------------
       PROBABILITY
       ------------------------------- */

    const probabilityValue =
        Number(
            data.probability
        );


    probability.textContent =
        `${probabilityValue}%`;


    probabilityFill.style.width =
        `${Math.min(
            probabilityValue,
            100
        )}%`;


    /* -------------------------------
       NOTIFICATION
       ------------------------------- */

    notificationBox.className =
        `notification-box ${
            data.status
        }`;


    notificationMessage.textContent =
        data.notification;


    if (data.email_sent) {

        notificationStatus.innerHTML = `

            <span>
                ✓ Email alert sent successfully
            </span>

            to

            <strong>
                ${escapeHtml(
                    data.recipient
                )}
            </strong>

        `;

    } else {

        notificationStatus.innerHTML = `

            <span>
                ${escapeHtml(
                    data.email_status
                )}
            </span>

        `;

    }


    /* -------------------------------
       TIME
       ------------------------------- */

    resultTime.textContent =
        new Date().toLocaleTimeString();

}


/* =========================================================
   PREDICTION ERROR
   ========================================================= */

function displayPredictionError(
    message
) {

    const result =
        document.getElementById(
            "result"
        );


    const notificationBox =
        document.getElementById(
            "notification-box"
        );


    const notificationMessage =
        document.getElementById(
            "notification-message"
        );


    const notificationStatus =
        document.getElementById(
            "notification-status"
        );


    result.className =
        "result neutral";


    result.innerHTML = `

        <div class="result-icon">
            !
        </div>

        <div>

            <strong>
                Prediction failed
            </strong>

            <small>
                ${escapeHtml(message)}
            </small>

        </div>

    `;


    notificationBox.className =
        "notification-box";


    notificationMessage.textContent =
        "Unable to generate notification.";


    notificationStatus.textContent =
        message;

}


/* =========================================================
   REFRESH BUTTON
   ========================================================= */

function setupRefreshButton() {

    const button =
        document.getElementById(
            "refresh-data"
        );


    if (!button) {

        return;

    }


    button.addEventListener(
        "click",
        async function () {

            button.disabled = true;

            button.textContent =
                "↻ Loading...";


            await loadDashboardData();


            button.disabled = false;

            button.textContent =
                "↻ Refresh";

        }
    );

}


/* =========================================================
   NAVIGATION
   ========================================================= */

function setupNavigation() {

    const links =
        document.querySelectorAll(
            ".nav-item"
        );


    links.forEach(
        link => {

            link.addEventListener(
                "click",
                function () {

                    links.forEach(
                        item =>
                            item.classList.remove(
                                "active"
                            )
                    );


                    this.classList.add(
                        "active"
                    );

                }
            );

        }
    );

}


/* =========================================================
   TABLE ERROR
   ========================================================= */

function showTableError(
    message
) {

    const tbody =
        document.getElementById(
            "leak-table-body"
        );


    if (!tbody) {

        return;

    }


    tbody.innerHTML = `

        <tr>

            <td
                colspan="7"
                class="table-loading"
            >

                ${escapeHtml(message)}

            </td>

        </tr>

    `;

}


/* =========================================================
   NUMBER FORMAT
   ========================================================= */

function formatNumber(
    value
) {

    if (
        value === null ||
        value === undefined ||
        value === ""
    ) {

        return "-";

    }


    const number =
        Number(value);


    if (
        Number.isNaN(number)
    ) {

        return "-";

    }


    return number.toFixed(2);

}


/* =========================================================
   HTML ESCAPE
   ========================================================= */

function escapeHtml(
    value
) {

    const div =
        document.createElement(
            "div"
        );


    div.textContent =
        String(value ?? "");


    return div.innerHTML;

}
