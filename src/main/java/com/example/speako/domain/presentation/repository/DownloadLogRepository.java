package com.example.speako.domain.presentation.repository;

import com.example.speako.domain.presentation.entity.DownloadLog;
import org.springframework.data.jpa.repository.JpaRepository;

public interface DownloadLogRepository extends JpaRepository<DownloadLog, Long> {
}
