/**
 * EasyTravel - Admin Booking Auto-fill & Dynamic Pricing
 * Automatise la saisie des réservations dans le Django Admin :
 * 1. Recherche et sélection d'un compte utilisateur -> auto-complète nom, téléphone, email, pièce d'identité.
 * 2. Sélection d'un départ -> charge dynamiquement les arrêts/villes de la ligne.
 * 3. Sélection des arrêts ou modification du nombre de places -> calcule automatiquement le tarif officiel et l'enregistre.
 */

(function($) {
    'use strict';

    $(document).ready(function() {
        const userSelect = $('#id_user');
        const departureSelect = $('#id_departure');
        const startStopSelect = $('#id_departure_stop');
        const endStopSelect = $('#id_arrival_stop');
        const seatsInput = $('#id_seats_reserved');
        const totalAmountInput = $('#id_total_amount');
        const agencyAmountInput = $('#id_agency_amount');
        const platformFeeInput = $('#id_platform_fee');

        function showHelperNote(target, text, isSuccess) {
            target.parent().find('.booking-helper-note').remove();
            const color = isSuccess ? '#059669' : '#b45309';
            const bg = isSuccess ? '#ecfdf5' : '#fffbeb';
            const border = isSuccess ? '#a7f3d0' : '#fde68a';
            const note = $('<div class="booking-helper-note" style="margin-top: 6px; padding: 6px 12px; border-radius: 6px; font-size: 12px; font-weight: 500; color: ' + color + '; background: ' + bg + '; border: 1px solid ' + border + ';">' + text + '</div>');
            target.after(note);
        }

        function highlightField(field) {
            field.css({ 'background-color': '#f0fdf4', 'transition': 'background-color 0.5s' });
            setTimeout(function() {
                field.css({ 'background-color': '', 'transition': 'background-color 0.5s' });
            }, 1200);
        }

        // 1. AUTO-COMPLÉTION LORS DE LA SÉLECTION D'UN VOYAGEUR
        userSelect.on('change', function() {
            const userId = $(this).val();
            if (!userId) {
                userSelect.parent().find('.booking-helper-note').remove();
                return;
            }

            $.ajax({
                url: '/admin/bookings/booking/api/user-info/' + userId + '/',
                method: 'GET',
                dataType: 'json',
                success: function(data) {
                    if (data.status === 'success') {
                        if (data.traveler_name) {
                            $('#id_traveler_name').val(data.traveler_name);
                            highlightField($('#id_traveler_name'));
                        }
                        if (data.traveler_phone) {
                            $('#id_traveler_phone').val(data.traveler_phone);
                            highlightField($('#id_traveler_phone'));
                        }
                        if (data.traveler_email) {
                            $('#id_traveler_email').val(data.traveler_email);
                            highlightField($('#id_traveler_email'));
                        }
                        if (data.id_type) {
                            $('#id_id_type').val(data.id_type);
                            highlightField($('#id_id_type'));
                        }
                        if (data.id_number) {
                            $('#id_id_number').val(data.id_number);
                            highlightField($('#id_id_number'));
                        }

                        showHelperNote(
                            userSelect, 
                            '✓ Données chargées depuis le compte : ' + (data.traveler_name || 'Utilisateur') + ' (' + (data.traveler_phone || 'Sans tel') + ')', 
                            true
                        );
                    }
                },
                error: function() {
                    console.warn('[EasyTravel Admin] Erreur lors du chargement des données utilisateur.');
                }
            });
        });

        // 2. CHARGEMENT DYNAMIQUE DES ARRÊTS LORS DU CHOIX DU DÉPART
        departureSelect.on('change', function() {
            const departureId = $(this).val();
            if (!departureId) {
                departureSelect.parent().find('.booking-helper-note').remove();
                return;
            }

            $.ajax({
                url: '/admin/bookings/booking/api/departure-info/' + departureId + '/',
                method: 'GET',
                dataType: 'json',
                success: function(data) {
                    if (data.status === 'success') {
                        const currentStart = startStopSelect.val();
                        const currentEnd = endStopSelect.val();

                        startStopSelect.empty();
                        endStopSelect.empty();

                        const stops = data.stops || [];
                        if (stops.length > 0) {
                            stops.forEach(function(s, idx) {
                                const isFirst = (idx === 0);
                                const isLast = (idx === stops.length - 1);
                                const tag = isFirst ? ' (Point de Départ)' : (isLast ? ' (Terminus)' : '');
                                const label = s.city_name + tag + ' [Barème: ' + s.price + ' FCFA]';
                                
                                startStopSelect.append($('<option>', {
                                    value: s.id,
                                    text: label,
                                    selected: currentStart ? (currentStart == s.id) : isFirst
                                }));
                                endStopSelect.append($('<option>', {
                                    value: s.id,
                                    text: label,
                                    selected: currentEnd ? (currentEnd == s.id) : isLast
                                }));
                            });
                        } else {
                            startStopSelect.append($('<option>', { value: '', text: 'Aucun arrêt configuré pour cette ligne' }));
                            endStopSelect.append($('<option>', { value: '', text: 'Aucun arrêt configuré pour cette ligne' }));
                        }

                        highlightField(startStopSelect);
                        highlightField(endStopSelect);

                        showHelperNote(
                            departureSelect, 
                            '✓ Ligne ' + data.line_name + ' (' + data.agency_name + ') • ' + data.available_capacity + ' place(s) disponible(s)', 
                            true
                        );

                        // Mettre à jour le tarif immédiatement
                        updatePricing();
                    }
                },
                error: function() {
                    console.warn('[EasyTravel Admin] Impossible de récupérer les informations du départ.');
                }
            });
        });

        // 3. CALCUL DYNAMIQUE DU TARIF DU TRONÇON
        function updatePricing() {
            const departureId = departureSelect.val();
            const startId = startStopSelect.val();
            const endId = endStopSelect.val();
            const seats = parseInt(seatsInput.val()) || 1;

            if (!departureId) return;

            $.ajax({
                url: '/admin/bookings/booking/api/calculate-price/',
                method: 'GET',
                data: {
                    departure_id: departureId,
                    start_id: startId,
                    end_id: endId,
                    seats: seats
                },
                dataType: 'json',
                success: function(data) {
                    if (data.status === 'success') {
                        if (totalAmountInput.length) {
                            totalAmountInput.val(data.total_amount);
                            highlightField(totalAmountInput);
                        }
                        if (agencyAmountInput.length) {
                            agencyAmountInput.val(data.agency_amount);
                        }
                        if (platformFeeInput.length) {
                            platformFeeInput.val(data.platform_fee);
                        }

                        showHelperNote(
                            totalAmountInput, 
                            '✓ Montant calculé automatiquement : ' + data.unit_price + ' FCFA × ' + seats + ' place(s) = ' + data.total_amount + ' FCFA', 
                            true
                        );
                    }
                }
            });
        }

        startStopSelect.on('change', updatePricing);
        endStopSelect.on('change', updatePricing);
        seatsInput.on('input change', updatePricing);

        // Si le formulaire est ouvert avec un départ déjà sélectionné (ex: modification)
        if (departureSelect.val() && (!startStopSelect.val() || !endStopSelect.val())) {
            departureSelect.trigger('change');
        }
    });
})(django.jQuery || jQuery);
