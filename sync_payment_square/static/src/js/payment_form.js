odoo.define('sync_payment_square.payment_form', function (require) {
"use strict";

var ajax = require('web.ajax');
var core = require('web.core');
var PaymentForm = require('payment.payment_form');

var _t = core._t;

PaymentForm.include({
    /**
     * @override
     */
    payEvent: function (ev) {
        ev.preventDefault();
        var $checkedRadio = this.$('input[type="radio"]:checked');
        // first we check that the user has selected a square as s2s payment method
        if ($checkedRadio.length === 1 && this.isNewPaymentRadio($checkedRadio) && $checkedRadio.data('provider') === 'square') {
            return this._createSquareNonce(ev, $checkedRadio);
        } else {
            return this._super.apply(this, arguments);
        }
    },

    addPmEvent: function (ev) {
        ev.stopPropagation();
        ev.preventDefault();
        var $checkedRadio = this.$('input[type="radio"]:checked');

        if ($checkedRadio.length === 1 && this.isNewPaymentRadio($checkedRadio) && $checkedRadio.data('provider') === 'square') {
            return this._createSquareNonce(ev, $checkedRadio, true);
        } else {
            return this._super.apply(this, arguments);
        }
    },

    _createSquareNonce: function (ev, $checkedRadio, addPmEvent) {
        ev.preventDefault();
        var self = this;
        if (ev.type === 'submit') {
            var button = $(ev.target).find('*[type="submit"]')[0]
        } else {
            var button = ev.target;
        }
        this.disableButton(button);
        var acquirerID = this.getAcquirerIdFromRadio($checkedRadio);
        var acquirerForm = this.$('#o_payment_add_token_acq_' + acquirerID);
        var inputsForm = $('input', acquirerForm);
        var formData = self.getFormData(inputsForm);
        if (this.options.partnerId === undefined) {
            console.warn('payment_form: unset partner_id when adding new token; things could go wrong');
        }
        var paymentForm = this.paymentForm;
        this.addPmEvent = false
        if (addPmEvent) {
            this.addPmEvent = true;
        }
        paymentForm.requestCardNonce();
    },

    _bindSquareCard: function ($checkedRadio) {
        var self = this;
        var acquirer_id = this.getAcquirerIdFromRadio($checkedRadio);
        var acquirer_form = this.$('#o_payment_add_token_acq_' + acquirer_id);
        var input_form = $('input', acquirer_form);
        var formData = this.getFormData(input_form);
        const paymentForm = new SqPaymentForm({
            applicationId: formData.application_id,
            card: {
                elementId: 'sq-card',
            },
            callbacks: {
                /*
                * callback function: cardNonceResponseReceived
                * Triggered when: SqPaymentForm completes a card nonce request
                */
                cardNonceResponseReceived: function (errors, nonce, cardData) {
                    if (errors) {
                        errors.forEach(function (error) {
                            self.displayError(
                                _t('Unable to save card'),
                                _t(error.message)
                            );
                        });
                        return;
                    } else {
                        self.cardData = cardData;
                        self.square_card_nonce = nonce;
                        if (self.addPmEvent) {
                            formData.verify_validity = true;
                        }
                        if (cardData && nonce) {
                            formData.card_data = cardData;
                            formData.payment_nonce = nonce;
                            return ajax.jsonRpc('/payment/square/s2s/create_json_3ds', 'call', formData).then(function (data) {
                                if (self.addPmEvent) {
                                    if (formData.return_url) {
                                        window.location = formData.return_url;
                                    } else {
                                        window.location.reload();
                                    }
                                } else {
                                    $checkedRadio.val(data.id);
                                    self.el.submit();
                                }
                            }).guardedCatch(function (error) {
                                if (error && error.data) {
                                  self.displayError(
                                        _t('Unable to save card'),
                                        _t("We are not able to add your payment method at the moment.") + error.data.message
                                    );
                                } else {
                                    self.displayError(
                                        _t('Unable to save card'),
                                        _t("We are not able to add your payment method at the moment. ")
                                    );
                                }
                            });
                        } else {
                            return self.displayError(
                                _t('Unable to save card'),
                                _t("We are not able to add your payment method at the moment. ")
                            );
                        }
                    }
                }
            }
        });
        paymentForm.build();
        this.paymentForm = paymentForm
    },
    /**
     * destroys the card element and any stripe instance linked to the widget.
     *
     * @private
     */
    _unbindSquareCard: function () {
        if (this.paymentForm) {
            this.paymentForm.destroy();
        }
        this.paymentForm = undefined;
        this.square_card_nonce = undefined;
        this.cardData = {};
    },
    _ajaxloadJSSquare: function (state) {
        if (state === 'enabled') {
            return ajax.loadJS("https://js.squareup.com/v2/paymentform");
        } else {
            return ajax.loadJS("https://js.squareupsandbox.com/v2/paymentform");
        }
    },
    /**
     * @override
     */
    updateNewPaymentDisplayStatus: function () {
        var self = this;
        var $checkedRadio = this.$('input[type="radio"]:checked');
        var provider = $checkedRadio.data('provider');
        var state = $checkedRadio.data('state');
        if ($checkedRadio.length !== 1) {
            return;
        }
        var def;
        if (provider === 'square') {
            def = this._ajaxloadJSSquare(state)
            $.when(def).then(function() {
                // always re-init Square (in case of multiple acquirers for Square, make sure the square instance is using the right key)
                self._unbindSquareCard();
                if (self.isNewPaymentRadio($checkedRadio)) {
                    self._bindSquareCard($checkedRadio);
                }
            });
        }
        return this._super.apply(this, arguments);
    },
    });
});
