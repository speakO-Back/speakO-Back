package com.example.speako.domain.recording.repository;

import com.example.speako.domain.recording.entity.VoiceRecording;
import org.springframework.data.jpa.repository.JpaRepository;

public interface VoiceRecordingRepository extends JpaRepository<VoiceRecording, Long> {
}
