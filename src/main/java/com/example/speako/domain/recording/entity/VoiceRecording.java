package com.example.speako.domain.recording.entity;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import java.time.LocalDateTime;

@Entity
@Table(name = "voice_recordings")
@Getter
@Builder
@AllArgsConstructor
@NoArgsConstructor
public class VoiceRecording {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long recordingId;

    private Long scriptId;
    private Long userId;

    private String audioFileUrl;
    private Integer duration;

    private LocalDateTime recordedAt = LocalDateTime.now();
}