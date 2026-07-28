(() => {
    "use strict";

    const currencyFormatter = new Intl.NumberFormat("ru-RU", {
        maximumFractionDigits: 0,
    });

    const formatPrice = (value) => `${currencyFormatter.format(value)} ₽`;
    const toNumber = (value) => Number.parseInt(value, 10) || 0;

    const initAvatarFallback = () => {
        const avatar = document.querySelector("[data-avatar]");
        const image = document.querySelector("[data-avatar-image]");
        const fallback = document.querySelector("[data-avatar-fallback]");

        if (!avatar || !image) {
            return;
        }

        const showFallback = () => {
            avatar.classList.add("avatar--fallback");
            image.hidden = true;
            fallback?.setAttribute("aria-hidden", "false");
        };

        image.addEventListener("error", showFallback, { once: true });

        if (image.complete && image.naturalWidth === 0) {
            showFallback();
        }
    };

    const initPlaceholderLinks = () => {
        const links = document.querySelectorAll("[data-placeholder-link]");
        const status = document.querySelector("[data-social-status]");

        links.forEach((link) => {
            link.addEventListener("click", (event) => {
                event.preventDefault();
                if (status) {
                    status.textContent = "Контактная ссылка скоро появится.";
                }
            });
        });
    };

    const initCalculator = () => {
        const serviceSelect = document.querySelector("[data-service-select]");
        const optionInputs = Array.from(
            document.querySelectorAll(".option-card__input[data-price]")
        );
        const baseOutput = document.querySelector("[data-base-total]");
        const optionsOutput = document.querySelector("[data-options-total]");
        const totalOutput = document.querySelector("[data-total]");
        const totalInput = document.querySelector("[data-total-input]");

        if (!serviceSelect || !baseOutput || !optionsOutput || !totalOutput) {
            return;
        }

        const updateTotal = () => {
            const selectedService =
                serviceSelect.options[serviceSelect.selectedIndex];
            const basePrice = toNumber(selectedService?.dataset.price);
            const optionsPrice = optionInputs.reduce((sum, input) => {
                return input.checked ? sum + toNumber(input.dataset.price) : sum;
            }, 0);
            const total = basePrice + optionsPrice;

            baseOutput.textContent = formatPrice(basePrice);
            optionsOutput.textContent = formatPrice(optionsPrice);
            totalOutput.textContent = formatPrice(total);

            if (totalInput) {
                totalInput.value = String(total);
            }
        };

        serviceSelect.addEventListener("change", updateTotal);
        optionInputs.forEach((input) => input.addEventListener("change", updateTotal));
        updateTotal();
    };

    const initCharacterCounter = () => {
        const textarea = document.querySelector("#comment");
        const counter = document.querySelector("[data-character-counter]");

        if (!textarea || !counter) {
            return;
        }

        const updateCounter = () => {
            counter.textContent = `${textarea.value.length} / ${textarea.maxLength}`;
        };

        textarea.addEventListener("input", updateCounter);
        updateCounter();
    };

    const initFormValidation = () => {
        const form = document.querySelector("#request-form");

        if (!form) {
            return;
        }

        const submitButton = form.querySelector("[data-submit-button]");
        const submitLabel = form.querySelector("[data-submit-label]");
        const fields = Array.from(
            form.querySelectorAll("#service, #name, #phone, #email, #comment")
        );

        const errorMessages = {
            service: {
                valueMissing: "Выберите базовую услугу.",
            },
            name: {
                valueMissing: "Укажите, как к вам обращаться.",
                tooShort: "Имя должно содержать не менее 2 символов.",
                tooLong: "Имя получилось слишком длинным.",
            },
            phone: {
                valueMissing: "Укажите номер телефона.",
                tooShort: "Укажите номер полностью — не менее 7 цифр.",
                tooLong: "Проверьте номер телефона.",
                phoneFormat: "Используйте цифры, пробелы, скобки, «+» или «-».",
                phoneLength: "Укажите от 7 до 15 цифр номера телефона.",
            },
            email: {
                typeMismatch: "Проверьте формат email, например name@example.com.",
                tooLong: "Email получился слишком длинным.",
            },
            comment: {
                tooLong: "Комментарий не должен превышать 2000 символов.",
            },
        };

        const setError = (field, message) => {
            const output = form.querySelector(`[data-error-for="${field.name}"]`);
            if (output) {
                output.textContent = message;
            }
            field.setAttribute("aria-invalid", message ? "true" : "false");
        };

        const validateField = (field) => {
            const messages = errorMessages[field.name] || {};
            let message = "";

            field.setCustomValidity("");

            if (
                (field.name === "name" || field.name === "phone") &&
                !field.value.trim()
            ) {
                message = messages.valueMissing;
            } else if (field.validity.valueMissing) {
                message = messages.valueMissing || "Заполните это поле.";
            } else if (field.validity.typeMismatch) {
                message = messages.typeMismatch || "Проверьте формат значения.";
            } else if (field.validity.tooShort) {
                message = messages.tooShort || "Значение слишком короткое.";
            } else if (field.validity.tooLong) {
                message = messages.tooLong || "Значение слишком длинное.";
            } else if (
                field.name === "name" &&
                field.value.trim().length > 0 &&
                field.value.trim().length < 2
            ) {
                message = messages.tooShort;
            } else if (field.name === "phone" && field.value.trim()) {
                const digitCount = (field.value.match(/\d/g) || []).length;
                if (!/^\+?[0-9()\-\s]+$/.test(field.value.trim())) {
                    message = messages.phoneFormat;
                } else if (digitCount < 7 || digitCount > 15) {
                    message = messages.phoneLength;
                }
            }

            field.setCustomValidity(message);
            setError(field, message);
            return !message;
        };

        fields.forEach((field) => {
            const eventName = field.tagName === "SELECT" ? "change" : "input";
            field.addEventListener(eventName, () => validateField(field));
            field.addEventListener("blur", () => validateField(field));
        });

        form.addEventListener("submit", (event) => {
            const customChecksPass = fields
                .map((field) => validateField(field))
                .every(Boolean);

            if (!customChecksPass || !form.checkValidity()) {
                event.preventDefault();
                const firstInvalid = fields.find((field) => !field.validity.valid);
                firstInvalid?.focus();
                return;
            }

            if (form.dataset.submitting === "true") {
                event.preventDefault();
                return;
            }

            form.dataset.submitting = "true";
            form.setAttribute("aria-busy", "true");

            if (submitButton) {
                submitButton.disabled = true;
                submitButton.classList.add("is-loading");
            }
            if (submitLabel) {
                submitLabel.textContent = "Отправляем…";
            }
        });

        window.addEventListener("pageshow", () => {
            form.dataset.submitting = "false";
            form.removeAttribute("aria-busy");
            if (submitButton) {
                submitButton.disabled = false;
                submitButton.classList.remove("is-loading");
            }
            if (submitLabel) {
                submitLabel.textContent = "Обсудить проект";
            }
        });

        const summary = document.querySelector("[data-error-summary]");
        if (summary) {
            requestAnimationFrame(() => summary.focus({ preventScroll: true }));
        }
    };

    const initAdminOptions = () => {
        const labels = {
            crm: "CRM",
            messengers: "Telegram / WhatsApp",
            payments: "Онлайн-оплата",
            urgent: "Срочная разработка",
        };

        document.querySelectorAll(".js-options").forEach((container) => {
            const rawValue = container.textContent.trim();
            let options = [];

            try {
                const parsed = JSON.parse(rawValue);
                options = Array.isArray(parsed) ? parsed : [parsed];
            } catch {
                options = rawValue
                    .replace(/^\[|\]$/g, "")
                    .split(",")
                    .map((item) => item.trim().replace(/^['"]|['"]$/g, ""))
                    .filter(Boolean);
            }

            container.textContent = "";
            options.forEach((option) => {
                const tag = document.createElement("span");
                tag.className = "option-tag";
                tag.textContent = labels[option] || option;
                container.append(tag);
            });
        });
    };

    initAvatarFallback();
    initPlaceholderLinks();
    initCalculator();
    initCharacterCounter();
    initFormValidation();
    initAdminOptions();
})();
