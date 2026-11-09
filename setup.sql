USE `db_gigel_prod`;

CREATE TABLE IF NOT EXISTS `email_notification_queue` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `recipient_email` VARCHAR(100) NOT NULL,
  `activation_code` VARCHAR(6) NOT NULL,
  `source_user_id` INT(11) NOT NULL,
  `event_type` ENUM('insert', 'code_update') NOT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `status` ENUM('pending', 'processing', 'sent', 'failed') NOT NULL DEFAULT 'pending',
  `attempts` TINYINT UNSIGNED NOT NULL DEFAULT 0,
  `next_attempt_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `locked_at` DATETIME DEFAULT NULL,
  `sent_at` DATETIME DEFAULT NULL,
  `last_error` VARCHAR(500) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_email_queue_ready` (`status`, `next_attempt_at`, `id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

DELIMITER $$

CREATE TRIGGER `trg_admin_activation_email_insert`
AFTER INSERT ON `users_admin_activation_code`
FOR EACH ROW
BEGIN
  INSERT INTO `email_notification_queue`
    (`recipient_email`, `activation_code`, `source_user_id`, `event_type`)
  VALUES
    (NEW.`email`, NEW.`email_activation_code`, NEW.`id`, 'insert');
END$$

CREATE TRIGGER `trg_admin_activation_email_code_update`
AFTER UPDATE ON `users_admin_activation_code`
FOR EACH ROW
BEGIN
  IF NOT (OLD.`email_activation_code` <=> NEW.`email_activation_code`) THEN
    INSERT INTO `email_notification_queue`
      (`recipient_email`, `activation_code`, `source_user_id`, `event_type`)
    VALUES
      (NEW.`email`, NEW.`email_activation_code`, NEW.`id`, 'code_update');
  END IF;
END$$

DELIMITER ;
