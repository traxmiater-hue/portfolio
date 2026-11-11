<?php
// Приём заявки с формы расчёта и отправка письма менеджеру.
header('Content-Type: application/json; charset=utf-8');
$to = 'manager@example.ru';                       // почта менеджера
$f = fn($k) => trim(strip_tags($_POST[$k] ?? ''));
$phone = preg_replace('/\D/', '', $f('phone'));
if (strlen($phone) < 11 || !empty($_POST['website'])) {   // website — скрытое поле-ловушка для ботов
    http_response_code(422);
    exit(json_encode(['ok' => false, 'error' => 'Проверьте телефон']));
}
$body = "Новая заявка на расчёт\n\n"
      . "Маршрут: {$f('from')} → {$f('to')}, {$f('km')} км\n"
      . "Вес: {$f('t')} т, тип: {$f('type')}\n"
      . "Телефон: +{$phone}\n";
$ok = mail($to, '=?UTF-8?B?' . base64_encode('Заявка с сайта') . '?=', $body,
           "Content-Type: text/plain; charset=utf-8\r\nFrom: site@example.ru");
echo json_encode(['ok' => $ok]);
